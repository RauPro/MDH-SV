export interface InstanceConfig {
  schema_version: 1;
  public_url: string;
  public_repository: string;
  private_repository: string;
  public_repository_id: number;
  publisher_actor_id: number;
  mcp_allowed_hosts: readonly string[];
  editorial_email: string;
  reporting_responsible: string;
}
export type PublicInstanceConfig = Pick<InstanceConfig, 'public_url' | 'public_repository' | 'editorial_email' | 'reporting_responsible'>;
export function validInstanceHost(value: unknown): boolean;
export function validateInstanceConfig(value: unknown): InstanceConfig;
export function parseInstanceConfig(raw: string): InstanceConfig;
export function publicInstanceConfig(value: InstanceConfig): PublicInstanceConfig;
