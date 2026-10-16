import { loadInstanceConfig } from '../../server/instance.mjs';
import { publicInstanceConfig } from '../../shared/instance-config.mjs';

// Load the explicitly selected, validated instance. Never spread process.env into define.
export const instanceDefine = (environment = process.env) => ({
  __MDH_PUBLIC_INSTANCE__: JSON.stringify(publicInstanceConfig(loadInstanceConfig(environment))),
});

export function localApiTarget() {
  const port = Number(process.env.PORT ?? 3001);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) throw new Error('Invalid local API PORT.');
  return `http://127.0.0.1:${port}`;
}
