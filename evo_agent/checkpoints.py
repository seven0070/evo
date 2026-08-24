from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

from .models import new_id
from .storage import SQLiteStore


class CheckpointManager:
    def __init__(self, workspace: Path, store: SQLiteStore):
        self.workspace = Path(workspace).resolve()
        self.store = store
        self.root = self.workspace / ".evo" / "checkpoints"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, task_id: str, label: str) -> Path:
        checkpoint_id = new_id("checkpoint")
        destination = self.root / checkpoint_id
        destination.mkdir(parents=True, exist_ok=False)
        for source in self.workspace.iterdir():
            if source.name == ".evo":
                continue
            target = destination / source.name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        created_at = datetime.now(timezone.utc).isoformat()
        self.store.add_checkpoint(checkpoint_id, task_id, label, str(destination), created_at)
        return destination

    def rollback(self, checkpoint_path: Path) -> None:
        checkpoint_path = Path(checkpoint_path).resolve()
        if checkpoint_path.parent != self.root:
            raise PermissionError("Checkpoint is not managed by this workspace")
        if not checkpoint_path.exists():
            raise FileNotFoundError(checkpoint_path)
        for source in self.workspace.iterdir():
            if source.name == ".evo":
                continue
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()
        for source in checkpoint_path.iterdir():
            target = self.workspace / source.name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
