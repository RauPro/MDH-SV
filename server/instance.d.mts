import type { InstanceConfig } from '../shared/instance-config.mjs';
export function loadInstanceConfig(environment?: Record<string, string | undefined>): InstanceConfig;
export const instance: InstanceConfig;
export let PUBLIC_URL: string;
export function configureLocalListener(port: number): void;
