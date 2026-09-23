import os
import shutil
from pathlib import Path
import pytest
from venice_key_manager.state import StateStore


@pytest.fixture
def temp_store(tmp_path):
    store = StateStore(data_dir=tmp_path)
    return store


def test_default_categories(temp_store):
    cats = temp_store.get_categories()
    assert "Default" in cats
    assert "Agents" in cats
    assert "Production" in cats


def test_add_remove_category(temp_store):
    assert temp_store.add_category("TestCategory") is True
    assert "TestCategory" in temp_store.get_categories()

    # Duplicate should return False
    assert temp_store.add_category("TestCategory") is False

    # Remove
    assert temp_store.remove_category("TestCategory") is True
    assert "TestCategory" not in temp_store.get_categories()

    # Cannot remove Default
    assert temp_store.remove_category("Default") is False


def test_key_metadata_and_threshold(temp_store):
    key_id = "test-key-123"
    meta = temp_store.update_key_meta(
        key_id=key_id,
        category="CustomCat",
        custom_threshold=0.15,
        notes="Worker key"
    )
    assert meta["category"] == "CustomCat"
    assert meta["custom_threshold"] == 0.15

    loaded = temp_store.get_key_meta(key_id)
    assert loaded["category"] == "CustomCat"
    assert loaded["notes"] == "Worker key"


def test_global_threshold(temp_store):
    temp_store.set_global_threshold(0.50)
    assert temp_store.get_global_threshold() == 0.50


def test_backup_export_and_import(temp_store, tmp_path):
    temp_store.add_category("ExportCat")
    temp_store.set_global_threshold(0.35)
    temp_store.update_key_meta("key-abc", category="ExportCat", custom_threshold=0.25)

    backup = temp_store.export_backup()
    assert backup.global_threshold == 0.35
    assert "ExportCat" in backup.categories

    # Import into fresh store
    new_dir = tmp_path / "imported"
    new_store = StateStore(data_dir=new_dir)
    assert "ExportCat" not in new_store.get_categories()

    success = new_store.import_backup(backup.model_dump())
    assert success is True
    assert "ExportCat" in new_store.get_categories()
    assert new_store.get_global_threshold() == 0.35
    assert new_store.get_key_meta("key-abc")["category"] == "ExportCat"
