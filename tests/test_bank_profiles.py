import json
import os

from core.bank_profiles import BankProfile, load_bank_profiles, save_bank_profile


def test_save_and_load_round_trip(tmp_path):
    profile = BankProfile(
        bank_slug="wells_fargo",
        display_name="Wells Fargo",
        deposit_headers=["deposits and other credits"],
        withdrawal_headers=["withdrawals and other debits"],
        checks_headers=["checks paid"],
        daily_balance_skip_headers=["daily ledger balance"],
    )
    save_bank_profile(profile, profiles_dir=str(tmp_path))

    loaded = load_bank_profiles(profiles_dir=str(tmp_path))
    assert len(loaded) == 1
    assert loaded[0].bank_slug == "wells_fargo"
    assert loaded[0].deposit_headers == ["deposits and other credits"]


def test_load_returns_empty_list_for_missing_directory(tmp_path):
    assert load_bank_profiles(profiles_dir=str(tmp_path / "does_not_exist")) == []


def test_malformed_profile_file_is_skipped_not_raised(tmp_path):
    good = BankProfile(bank_slug="good_bank")
    save_bank_profile(good, profiles_dir=str(tmp_path))

    with open(os.path.join(str(tmp_path), "broken.json"), "w") as f:
        f.write("{not valid json")

    loaded = load_bank_profiles(profiles_dir=str(tmp_path))
    assert len(loaded) == 1
    assert loaded[0].bank_slug == "good_bank"


def test_re_saving_same_slug_overwrites_not_duplicates(tmp_path):
    save_bank_profile(BankProfile(bank_slug="chase", deposit_headers=["a"]), profiles_dir=str(tmp_path))
    save_bank_profile(BankProfile(bank_slug="chase", deposit_headers=["b"]), profiles_dir=str(tmp_path))

    loaded = load_bank_profiles(profiles_dir=str(tmp_path))
    assert len(loaded) == 1
    assert loaded[0].deposit_headers == ["b"]
