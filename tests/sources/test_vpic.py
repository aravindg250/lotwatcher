from datetime import timedelta

import httpx2
import pytest

from lotwatcher.sources.client import UnexpectedResponse
from lotwatcher.sources.settings import VPIC, build_client
from lotwatcher.sources.vpic import decode_vin

from .conftest import ScriptedServer, ok, recorded

EXAMPLE_VIN = "1HGCM82633A004352"
BAD_CHECK_DIGIT_VIN = "1HGCM82633A004353"
YEAR = timedelta(days=365).total_seconds()


@pytest.fixture
def vpic(db, clock):
    def make(server: ScriptedServer):
        return build_client(VPIC, db, transport=httpx2.MockTransport(server), clock=clock)

    return make


def test_decode_reads_the_main_columns(vpic):
    server = ScriptedServer(recorded("vpic/decode_1HGCM82633A004352"))

    decoded = decode_vin(vpic(server), EXAMPLE_VIN)

    assert decoded.vin == EXAMPLE_VIN
    assert (decoded.year, decoded.make, decoded.model, decoded.trim) == (
        2003,
        "HONDA",
        "Accord",
        "EX-V6",
    )
    assert decoded.body_style == "Coupe"
    assert decoded.engine == "3.0L 6-cyl Gasoline"


def test_missing_values_are_none_not_empty_text(vpic):
    # This VIN's DriveType is empty in the real response
    server = ScriptedServer(recorded("vpic/decode_1HGCM82633A004352"))

    assert decode_vin(vpic(server), EXAMPLE_VIN).drivetrain is None


def test_specs_keep_only_filled_in_fields(vpic):
    server = ScriptedServer(recorded("vpic/decode_1HGCM82633A004352"))

    specs = decode_vin(vpic(server), EXAMPLE_VIN).specs

    assert specs["EngineHP"] == "240"
    assert specs["TransmissionStyle"] == "Automatic"
    assert "DriveType" not in specs
    assert all(value not in ("", None, "Not Applicable") for value in specs.values())


def test_a_clean_decode_is_marked_clean(vpic):
    server = ScriptedServer(recorded("vpic/decode_1HGCM82633A004352"))

    decoded = decode_vin(vpic(server), EXAMPLE_VIN)

    assert decoded.clean is True
    assert decoded.error_codes == ("0",)


def test_a_bad_check_digit_is_not_clean_even_though_vpic_still_decodes_it(vpic):
    server = ScriptedServer(recorded("vpic/decode_bad_check_digit"))

    decoded = decode_vin(vpic(server), BAD_CHECK_DIGIT_VIN)

    assert decoded.clean is False
    assert decoded.error_codes == ("1",)
    assert decoded.error_text.startswith("1 - Check Digit")
    assert decoded.make == "HONDA"  # the trap: it still looks like a real car


def test_several_error_codes_are_all_kept(vpic):
    # Hand-written: vPIC lists several codes separated by commas
    body = {"Results": [{"VIN": EXAMPLE_VIN, "ErrorCode": "1,5", "ErrorText": "1 - ...; 5 - ..."}]}

    decoded = decode_vin(vpic(ScriptedServer(ok(body))), EXAMPLE_VIN)

    assert decoded.error_codes == ("1", "5")
    assert decoded.clean is False


def test_request_uses_the_cleaned_up_vin_in_the_path(vpic):
    server = ScriptedServer(recorded("vpic/decode_1HGCM82633A004352"))

    decode_vin(vpic(server), "  1hgcm82633a004352 ")

    request = server.requests[0]
    assert request.url.path == f"/api/vehicles/DecodeVinValues/{EXAMPLE_VIN}"
    assert dict(request.url.params) == {"format": "json"}


def test_decodes_are_cached_forever(vpic, clock):
    server = ScriptedServer(recorded("vpic/decode_1HGCM82633A004352"))
    client = vpic(server)

    decode_vin(client, EXAMPLE_VIN)
    clock.advance(YEAR)
    decode_vin(client, EXAMPLE_VIN)

    assert len(server.requests) == 1


def test_a_response_without_one_result_raises_and_is_not_cached(vpic):
    good = recorded("vpic/decode_1HGCM82633A004352")
    server = ScriptedServer(ok({"Count": 0, "Results": []}), good)
    client = vpic(server)

    with pytest.raises(UnexpectedResponse, match="vpic"):
        decode_vin(client, EXAMPLE_VIN)

    assert decode_vin(client, EXAMPLE_VIN).make == "HONDA"
    assert len(server.requests) == 2
