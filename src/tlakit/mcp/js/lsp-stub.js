// The TLAPS language client is irrelevant to the MCP server; stub it out.
class Noop { constructor() {} start() { return Promise.resolve(); } stop() { return Promise.resolve(); }
    onNotification() {} sendRequest() { return Promise.resolve(); } onRequest() {} sendNotification() {} }
module.exports = new Proxy({}, { get: () => Noop });
