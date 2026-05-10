// Bun test preload — registers happy-dom globals (document, window,
// CSS, etc.) so DOM-touching modules can be tested without a browser.
import { GlobalRegistrator } from '@happy-dom/global-registrator';

GlobalRegistrator.register();
