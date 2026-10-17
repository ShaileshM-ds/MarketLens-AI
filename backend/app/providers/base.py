"""Shared source contract and optional capture capability."""
from typing import Protocol,Iterable
from app.services.normalize import parse_file
from app.services.demo import generate

class RecordProvider(Protocol):
    def records(self) -> Iterable[dict]: ...

class LocalFileProvider:
    def __init__(self,content): self.content=content
    def records(self): return parse_file(self.content)

class DemoProvider:
    def records(self): return generate()

class CaptureProvider(Protocol):
    """Future optional integration. Main product scraper does not promise captures."""
    def archived_capture(self,source_reference:str) -> dict|None: ...
