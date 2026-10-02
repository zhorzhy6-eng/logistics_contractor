"""Cancellation shared by document readers and remote recognizers."""


class ImportCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise ImportCancelled()
