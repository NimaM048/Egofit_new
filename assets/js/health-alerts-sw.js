self.addEventListener("push", function (event) {
    var payload = {};
    if (event.data) {
        try {
            payload = event.data.json();
        } catch (error) {
            payload = { body: event.data.text() };
        }
    }

    var destination = new URL(payload.url || "/accounts/coach/", self.location.origin);
    if (destination.origin !== self.location.origin) {
        destination = new URL("/accounts/coach/", self.location.origin);
    }

    event.waitUntil(self.registration.showNotification(payload.title || "تغییر وضعیت شاگرد", {
        body: payload.body || "تغییری در وضعیت یکی از شاگردان ثبت شده است.",
        icon: payload.icon || "/static/images/logoimageego.webp",
        badge: payload.badge || "/static/images/logoimageego.webp",
        tag: payload.tag || "egofit-health-alert",
        renotify: false,
        data: { url: destination.pathname + destination.search }
    }));
});

self.addEventListener("notificationclick", function (event) {
    event.notification.close();
    var requestedUrl = new URL(
        event.notification.data && event.notification.data.url || "/accounts/coach/",
        self.location.origin
    );
    if (requestedUrl.origin !== self.location.origin || requestedUrl.pathname.indexOf("/accounts/") !== 0) {
        requestedUrl = new URL("/accounts/coach/", self.location.origin);
    }

    event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clients) {
        for (var index = 0; index < clients.length; index += 1) {
            if (clients[index].url.indexOf(self.location.origin) === 0) {
                return clients[index].navigate(requestedUrl.href).then(function (client) {
                    return client ? client.focus() : self.clients.openWindow(requestedUrl.href);
                });
            }
        }
        return self.clients.openWindow(requestedUrl.href);
    }));
});
