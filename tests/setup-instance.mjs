import { fileURLToPath } from 'node:url';

// Tests describing historical upstream identities use a fixed fixture. Runtime
// code never reads this module; explicit fork tests launch a separate process.
process.env.MDH_INSTANCE_FILE = fileURLToPath(new URL('./fixtures/upstream-instance.json', import.meta.url));
