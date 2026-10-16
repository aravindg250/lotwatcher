import pytest

from lotwatcher.vin import check_digit, clean_vin, is_valid_vin

EXAMPLE_VIN = "1HGCM82633A004352"  # widely published example, check digit "3"


def test_check_digit_of_the_example_vin():
    assert check_digit(EXAMPLE_VIN) == "3"


def test_check_digit_can_be_x():
    # A value of 10 is written as "X"
    assert check_digit("11111111111111111") == "1"
    assert check_digit("1M8GDM9AXKP042788") == "X"


def test_example_vin_is_valid():
    assert is_valid_vin(EXAMPLE_VIN)


def test_wrong_check_digit_is_invalid():
    assert not is_valid_vin("1HGCM82633A004353")


@pytest.mark.parametrize(
    "vin",
    [
        "1HGCM82633A00435",  # 16 characters
        "1HGCM82633A0043521",  # 18 characters
        "1HGCM82633A00435O",  # O is never used in VINs
        "IHGCM82633A004352",  # nor I
        "QHGCM82633A004352",  # nor Q
        "",
    ],
)
def test_malformed_vins_are_invalid(vin):
    assert not is_valid_vin(vin)


def test_clean_vin_trims_and_uppercases():
    assert clean_vin("  1hgcm82633a004352 ") == EXAMPLE_VIN


def test_clean_vin_gives_none_for_blank():
    assert clean_vin("   ") is None
    assert clean_vin(None) is None
