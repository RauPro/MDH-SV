"""
Capa de Persistencia Serverless Híbrida (SQLite / DuckDB / Supabase compatible).
Almacena incidentes de Derechos Humanos con índices espaciales y temporales ($0 costo operativo).
"""

import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from mdh_schema import (
    DepartmentEnum,
    HumanRightsIncident,
    SeverityLevel,
    ViolationCategory,
)


class IncidentRepository:
    """Repositorio de persistencia relacional para incidentes y evidencias."""

    def __init__(self, db_path: str = "./data/mdh_database.sqlite"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Inicializa las tablas e índices si no existen."""
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                incident_date TEXT,
                category TEXT NOT NULL,
                violation_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                certainty TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                department TEXT NOT NULL,
                municipality TEXT NOT NULL,
                specific_location TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                under_state_of_exception INTEGER NOT NULL DEFAULT 1,
                penitentiary_related INTEGER NOT NULL DEFAULT 0,
                prison_name TEXT,
                sources_count INTEGER NOT NULL DEFAULT 1,
                victims_count INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

            # Índices para consultas ultra-rápidas
            conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_dept ON incidents(department);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_date ON incidents(incident_date);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_cat ON incidents(category);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_prison ON incidents(penitentiary_related, prison_name);")
            conn.commit()

    def save_incident(self, incident: HumanRightsIncident) -> str:
        """Guarda o actualiza un expediente de incidente."""
        raw_json = incident.model_dump_json()
        date_str = incident.incident_date.isoformat() if incident.incident_date else None
        now_str = datetime.now(timezone.utc).isoformat()

        cat_val = incident.category.value if hasattr(incident.category, "value") else str(incident.category or "")
        violation_val = incident.violation_type.value if hasattr(incident.violation_type, "value") else str(incident.violation_type or "")
        sev_val = incident.severity.value if hasattr(incident.severity, "value") else str(incident.severity or "")
        cert_val = incident.certainty.value if hasattr(incident.certainty, "value") else str(incident.certainty or "")
        dept_val = incident.department.value if hasattr(incident.department, "value") else str(incident.department or "")

        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO incidents (
                id, incident_date, category, violation_type, severity, certainty,
                title, summary, department, municipality, specific_location,
                latitude, longitude, under_state_of_exception, penitentiary_related,
                prison_name, sources_count, victims_count, raw_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                incident_date=excluded.incident_date,
                category=excluded.category,
                violation_type=excluded.violation_type,
                severity=excluded.severity,
                certainty=excluded.certainty,
                title=excluded.title,
                summary=excluded.summary,
                department=excluded.department,
                municipality=excluded.municipality,
                specific_location=excluded.specific_location,
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                under_state_of_exception=excluded.under_state_of_exception,
                penitentiary_related=excluded.penitentiary_related,
                prison_name=excluded.prison_name,
                sources_count=excluded.sources_count,
                victims_count=excluded.victims_count,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """, (
                incident.id,
                date_str,
                cat_val,
                violation_val,
                sev_val,
                cert_val,
                incident.title,
                incident.summary,
                dept_val,
                incident.municipality,
                incident.specific_location,
                incident.coordinates.latitude,
                incident.coordinates.longitude,
                1 if incident.under_state_of_exception else 0,
                1 if incident.penitentiary_related else 0,
                incident.prison_name,
                len(incident.sources),
                incident.victims_count if incident.victims_count is not None else len(incident.victims),
                raw_json,
                incident.created_at.isoformat(),
                now_str
            ))
            conn.commit()
        return incident.id

    def get_incident_by_id(self, incident_id: str) -> Optional[HumanRightsIncident]:
        """Recupera el expediente completo por su ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT raw_json FROM incidents WHERE id = ?", (incident_id,))
            row = cursor.fetchone()
            if row:
                data = json.loads(row["raw_json"])
                return HumanRightsIncident.model_validate(data)
        return None

    def query_incidents(
        self,
        department: Optional[str] = None,
        category: Optional[str] = None,
        severity: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        penitentiary_only: bool = False,
        search_query: Optional[str] = None,
        limit: int = 100
    ) -> List[HumanRightsIncident]:
        """Consulta avanzada con múltiples filtros combinables."""
        conditions = []
        params = []

        if department:
            conditions.append("department = ?")
            params.append(department)

        if category:
            conditions.append("category = ?")
            params.append(category)

        if severity:
            conditions.append("severity = ?")
            params.append(severity)

        if start_date:
            conditions.append("incident_date >= ?")
            params.append(start_date.isoformat())

        if end_date:
            conditions.append("incident_date <= ?")
            params.append(end_date.isoformat())

        if penitentiary_only:
            conditions.append("penitentiary_related = 1")

        if search_query:
            conditions.append("(title LIKE ? OR summary LIKE ? OR specific_location LIKE ?)")
            wildcard = f"%{search_query}%"
            params.extend([wildcard, wildcard, wildcard])

        if not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT raw_json FROM incidents {where_clause} ORDER BY incident_date DESC LIMIT ?"
        params.append(limit)

        results: List[HumanRightsIncident] = []
        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            for row in cursor.fetchall():
                data = json.loads(row["raw_json"])
                results.append(HumanRightsIncident.model_validate(data))

        return results

    def count_total(self) -> int:
        """Devuelve el total de incidentes registrados."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) as total FROM incidents")
            return cursor.fetchone()["total"]

    def delete_incident(self, incident_id: str) -> bool:
        """Elimina un expediente por ID (útil para depuración de falsos positivos)."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
            conn.commit()
            return cursor.rowcount > 0

    def has_source_url(self, url: str) -> bool:
        with self._get_connection() as conn:
            return conn.execute("SELECT 1 FROM incidents, json_each(raw_json, '$.sources') AS source WHERE json_extract(source.value, '$.original_url') = ? LIMIT 1", (url,)).fetchone() is not None
