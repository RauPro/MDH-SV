export type OfficialStatisticsDepartment = 'Ahuachapán' | 'Santa Ana' | 'Sonsonate' | 'Chalatenango' | 'La Libertad' | 'San Salvador' | 'Cuscatlán' | 'La Paz' | 'Cabañas' | 'San Vicente' | 'Usulután' | 'San Miguel' | 'Morazán' | 'La Unión';
export const OFFICIAL_STATISTICS_DEPARTMENTS: readonly OfficialStatisticsDepartment[];

export type OfficialStatisticsValue =
  | { value: number; status: 'available'; locator: string }
  | { value: null; status: 'not_available'; locator: null };
export type OfficialStatisticsMonth = { month: number } & (
  | OfficialStatisticsValue
  | { value: null; status: 'outside_cutoff'; locator: null }
);
export interface OfficialStatisticsSource {
  id: string;
  institution: string;
  title: string;
  url: string;
  format: 'pdf' | 'html' | 'xlsx';
  retrieved_at: string;
  content_sha256: string;
  edition_note: string;
}
export interface OfficialStatisticsPeriod {
  year: number;
  start_date: string;
  end_date: string;
  completeness: 'complete' | 'partial' | 'unavailable';
  source_id: string;
  indicator: 'homicides' | 'intentional_homicides';
  unit: 'persons' | 'source_reported_homicides';
  methodology: string;
  notes: string[];
  national: OfficialStatisticsValue;
  departments: Array<{ department: OfficialStatisticsDepartment } & OfficialStatisticsValue>;
  months: OfficialStatisticsMonth[] | null;
}
export interface OfficialStatistics {
  schema_version: 1;
  dataset_id: 'sv-official-homicides';
  title: string;
  as_of: string;
  generated_at: string;
  coverage_start_year: 2010;
  comparability_note: string;
  sources: OfficialStatisticsSource[];
  periods: OfficialStatisticsPeriod[];
}
export type OfficialStatisticsRow = Omit<OfficialStatisticsPeriod, 'national' | 'departments' | 'source_id'>
  & OfficialStatisticsValue
  & { territory_level: 'national' | 'department'; department: 'national' | OfficialStatisticsDepartment; source: OfficialStatisticsSource };
export function parseOfficialStatistics(value: unknown): OfficialStatistics;
