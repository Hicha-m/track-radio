"""Run the production ranking refresher beside isolated browser test servers."""

from threading import Event, Thread

from app.services.prepared_rankings import refresh


def start(factory):
    refresh(factory)
    stop = Event()
    errors = []

    def run():
        while not stop.wait(0.5):
            try:
                refresh(factory)
            except Exception as error:
                errors.append(type(error).__name__)
                return

    thread = Thread(target=run, daemon=True)
    thread.start()

    def close():
        stop.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert not errors, errors

    return close
