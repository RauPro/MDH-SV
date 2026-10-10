"""Conservative duplicate detection for private candidates, never a truth assessment."""
from datetime import datetime, timezone
from mdh_schema import HumanRightsIncident


class IncidentDeduplicator:
    def __init__(self, max_days_diff=2, similarity_threshold=0.35):
        self.max_days_diff = max_days_diff
        self.similarity_threshold = similarity_threshold

    def are_same_incident(self, first: HumanRightsIncident, second: HumanRightsIncident) -> bool:
        # A single article can contain several different incidents. Only repeated
        # extraction of the SAME source AND content AND event payload is idempotent.
        sources1 = {(s.original_url, s.source_sha256) for s in first.sources if s.original_url and len(s.source_sha256) == 64}
        sources2 = {(s.original_url, s.source_sha256) for s in second.sources if s.original_url and len(s.source_sha256) == 64}
        return bool(sources1 & sources2) and all(getattr(first, key) == getattr(second, key)
            for key in ('title', 'incident_date', 'department', 'municipality', 'violation_type'))

    def merge_incidents(self, master, duplicate):
        if not self.are_same_incident(master, duplicate):
            raise ValueError('Distinct reports require editorial review before consolidation')
        known = {s.original_url for s in master.sources}
        for source in duplicate.sources:
            if source.original_url not in known:
                master.sources.append(source)
                known.add(source.original_url)
        master.updated_at = datetime.now(timezone.utc)
        # Source count does not establish independence or corroboration.
        return master

    def deduplicate_batch(self, incidents):
        unique = []
        for incident in incidents:
            match = next((item for item in unique if self.are_same_incident(item, incident)), None)
            if match is None:
                unique.append(incident)
            else:
                self.merge_incidents(match, incident)
        return unique
