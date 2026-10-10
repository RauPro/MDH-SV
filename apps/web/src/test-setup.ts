import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { value(this: HTMLDialogElement) { this.setAttribute('open', ''); }, configurable: true });
Object.defineProperty(HTMLDialogElement.prototype, 'close', { value(this: HTMLDialogElement) { this.removeAttribute('open'); }, configurable: true });
afterEach(() => { cleanup(); history.replaceState(null, '', '/'); });
