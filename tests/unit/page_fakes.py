"""Page event support for unit doubles used by the real discovery lifecycle."""


class EventPage:
    def __init__(self):
        self.listeners = {}

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)
        return self

    def remove_listener(self, event, callback):
        callbacks = self.listeners.get(event, [])
        if callback in callbacks:
            callbacks.remove(callback)
        return self

    def emit(self, event, *args):
        for callback in tuple(self.listeners.get(event, [])):
            callback(*args)
