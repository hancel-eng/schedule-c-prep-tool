import pytest
from core.deduplication import FilenameDeduplicator

class DummyFile:
    def __init__(self, name):
        self.name = name

def test_filename_deduplication_detects_duplicates():
    dedup = FilenameDeduplicator()
    
    files = [
        DummyFile("Bank_Statement_Jan2026.pdf"),
        DummyFile("Bank_Statement_Feb2026.pdf"),
        DummyFile("Bank_Statement_Jan2026.pdf") # Duplicate!
    ]

    unique, duplicates = dedup.process_files(files)

    assert len(unique) == 2
    assert len(duplicates) == 1
    assert duplicates[0]["filename"] == "Bank_Statement_Jan2026.pdf"
    assert "Duplicate filename detected" in duplicates[0]["reason"]

def test_distinct_filenames_preserved():
    dedup = FilenameDeduplicator()
    
    files = [
        DummyFile("Bank_Statement_Jan.pdf"),
        DummyFile("Bank_Statement_Feb.pdf"),
        DummyFile("Bank_Statement_Mar.pdf")
    ]

    unique, duplicates = dedup.process_files(files)

    assert len(unique) == 3
    assert len(duplicates) == 0
