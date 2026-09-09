import os
from typing import List, Dict, Tuple, Any

class FilenameDeduplicator:
    """
    Manages strict filename-based deduplication for uploaded files.
    Rule:
    - If a file with the exact same filename has already been processed for this client/year,
      mark it as a duplicate and skip processing to avoid double counting.
    - If no filename matches, treat the file as unique and retain ALL transactions inside.
    """

    def __init__(self):
        self.seen_filenames = set()

    def reset(self):
        self.seen_filenames.clear()

    def process_files(self, file_objects: List[Any]) -> Tuple[List[Any], List[Dict[str, str]]]:
        """
        Processes a list of file objects (e.g. UploadedFile or file dicts).
        Returns:
            unique_files: List of files that have unique names.
            duplicates_flagged: List of dicts describing skipped duplicate files.
        """
        unique_files = []
        duplicates_flagged = []

        for file_obj in file_objects:
            if hasattr(file_obj, 'name') and file_obj.name:
                filename = file_obj.name
            elif isinstance(file_obj, dict) and 'name' in file_obj:
                filename = file_obj['name']
            else:
                filename = str(file_obj)

            normalized_name = os.path.basename(filename).strip().lower()

            if normalized_name in self.seen_filenames:
                duplicates_flagged.append({
                    "filename": filename,
                    "reason": f"Duplicate filename detected: '{filename}' has already been uploaded for this client.",
                    "status": "Skipped (Duplicate Filename)"
                })
            else:
                self.seen_filenames.add(normalized_name)
                unique_files.append(file_obj)

        return unique_files, duplicates_flagged
