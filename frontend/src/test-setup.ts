import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => { cleanup(); window.history.replaceState(null, '', '/workspace/'); });
// jsdom supplies DOM layout but not the browser's modal top-layer behavior.
HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
HTMLElement.prototype.showPopover = function () { this.style.display = 'block'; };
HTMLElement.prototype.hidePopover = function () { this.style.display = 'none'; };
