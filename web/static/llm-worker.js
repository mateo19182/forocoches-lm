// WebLLM runs in a worker so a long prefill does not freeze the page.
import { WebWorkerMLCEngineHandler } from "/static/vendor/web-llm.js";
const handler = new WebWorkerMLCEngineHandler();
self.onmessage = (msg) => handler.onmessage(msg);
