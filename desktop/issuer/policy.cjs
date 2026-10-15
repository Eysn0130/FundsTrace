'use strict';
function trustedSender(event, contents, page) {
  return Boolean(contents && !contents.isDestroyed() && event?.senderFrame && event.sender === contents && event.senderFrame === contents.mainFrame && event.senderFrame.url === page);
}
function localResource(url, resources) { return resources.has(url); }
module.exports = { trustedSender, localResource };
