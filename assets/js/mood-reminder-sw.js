self.addEventListener("push", function (event) {
    var payload = {};
    if (event.data) {
        try {
            payload = event.data.json();
        } catch (error) {
            payload = { body: event.data.text() };
        }
    }

    var destination = new URL(payload.url || "/accounts/profile/mood/", self.location.origin);
    if (destination.origin !== self.location.origin) {
        destination = new URL("/accounts/profile/mood/", self.location.origin);
    }

    event.waitUntil(self.registration.showNotification(payload.title || "یادآور ایگوفیت", {
        body: payload.body || "وضعیت روزانه‌تان را ثبت کنید.",
        icon: payload.icon || "/static/images/logoimageego.webp",
        badge: payload.badge || "/static/images/logoimageego.webp",
        tag: "egofit-mood-reminder",
        renotify: false,
        data: { url: destination.pathname + destination.search }
    }));
});

self.addEventListener("notificationclick", function (event) {
    event.notification.close();
    var requestedUrl = new URL(
        event.notification.data && event.notification.data.url || "/accounts/profile/mood/",
        self.location.origin
    );
    if (requestedUrl.origin !== self.location.origin) {
        requestedUrl = new URL("/accounts/profile/mood/", self.location.origin);
    }

    event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clients) {
        for (var i = 0; i < clients.length; i += 1) {
            if (clients[i].url.indexOf(self.location.origin) === 0) {
                return clients[i].navigate(requestedUrl.href).then(function (client) {
                    return client ? client.focus() : self.clients.openWindow(requestedUrl.href);
                });
            }
        }
        return self.clients.openWindow(requestedUrl.href);
    }));
});
