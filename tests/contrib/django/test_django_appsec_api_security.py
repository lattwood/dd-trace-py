# -*- coding: utf-8 -*-
import base64
import gzip
import json

import pytest

from ddtrace.appsec import _constants
from ddtrace.settings.asm import config as asm_config
from tests.appsec.appsec.api_security.test_schema_fuzz import equal_with_meta
from tests.utils import override_global_config


def _aux_appsec_get_root_span(
    client,
    test_spans,
    tracer,
    payload=None,
    url="/",
    content_type="text/plain",
    headers=None,
    cookies=None,
):
    if cookies is None:
        cookies = {}
    tracer._asm_enabled = asm_config._asm_enabled
    tracer._iast_enabled = asm_config._iast_enabled
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    # Set cookies
    client.cookies.load(cookies)
    if payload is None:
        if headers:
            response = client.get(url, **headers)
        else:
            response = client.get(url)
    else:
        if headers:
            response = client.post(url, payload, content_type=content_type, **headers)
        else:
            response = client.post(url, payload, content_type=content_type)
    return test_spans.spans[0], response


def test_api_security(client, test_spans, tracer):
    import django

    with override_global_config(dict(_asm_enabled=True, _api_security_enabled=True, _api_security_sample_rate=1.0)):
        payload = {"key": "secret", "ids": [0, 1, 2, 3]}
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/path-params/2022/path_param/?y=0&x=1&y=2",
            payload=payload,
            cookies={"secret": "a1b2c3d4e5f6"},
            content_type="application/json",
        )
        assert response.status_code == 200

        assert asm_config._api_security_enabled
        assert asm_config._api_security_sample_rate == 1.0

        headers_schema = {
            "1": [
                {
                    "content-type": [8],
                    "content-length": [8],
                    "x-frame-options": [8],
                }
            ],
            "2": [
                {
                    "content-type": [8],
                    "content-length": [8],
                    "x-frame-options": [8],
                }
            ],
            "3": [
                {
                    "content-type": [8],
                    "x-content-type-options": [8],
                    "referrer-policy": [8],
                    "x-frame-options": [8],
                    "content-length": [8],
                }
            ],
            "4": [
                {
                    "content-type": [8],
                    "cross-origin-opener-policy": [8],
                    "x-content-type-options": [8],
                    "referrer-policy": [8],
                    "x-frame-options": [8],
                    "content-length": [8],
                }
            ],
        }

        for name, expected_value in [
            ("_dd.appsec.s.req.body", [{"key": [8], "ids": [[[4]], {"len": 4}]}]),
            (
                "_dd.appsec.s.req.headers",
                [{"content-length": [8], "content-type": [8]}],
            ),
            ("_dd.appsec.s.req.cookies", [{"secret": [8]}]),
            ("_dd.appsec.s.req.query", [{"y": [8], "x": [8]}]),
            ("_dd.appsec.s.req.params", [{"year": [4], "month": [8]}]),
            ("_dd.appsec.s.res.headers", headers_schema[django.__version__[0]]),
            ("_dd.appsec.s.res.body", [{"year": [4], "month": [8]}]),
        ]:
            value = root_span.get_tag(name)
            assert value, name
            api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
            assert equal_with_meta(api, expected_value), name


@pytest.mark.parametrize("parse_response_body", [False, True])
@pytest.mark.parametrize(
    ["name", "expected_value"],
    [
        ("_dd.appsec.s.req.body", [{"key": [8], "ids": [[[4]], {"len": 4}]}]),
        (
            "_dd.appsec.s.req.headers",
            [{"user-agent": [8], "content-length": [8], "content-type": [8]}],
        ),
        ("_dd.appsec.s.req.cookies", [{"secret": [8]}]),
        ("_dd.appsec.s.req.query", [{"y": [8], "x": [8]}]),
        ("_dd.appsec.s.req.params", [{"year": [4], "month": [8]}]),
        ("_dd.appsec.s.res.headers", [{"content-type": [8]}]),
        ("_dd.appsec.s.res.body", [{"errors": [[[{"detail": [8], "title": [8]}]], {"len": 1}]}]),
    ],
)
def test_api_security_with_srb(client, test_spans, tracer, parse_response_body, name, expected_value):
    """Test if srb is still working as expected with api security activated"""

    with override_global_config(
        dict(
            _asm_enabled=True,
            _api_security_enabled=True,
            _api_security_sample_rate=1.0,
            _api_security_parse_response_body=parse_response_body,
        )
    ):
        payload = {"key": "secret", "ids": [0, 1, 2, 3]}
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/path-params/2022/path_param/?y=0&x=1&y=xtrace",
            payload=payload,
            cookies={"secret": "a1b2c3d4e5f6"},
            content_type="application/json",
            headers={"HTTP_USER_AGENT": "dd-test-scanner-log-block"},
        )
        assert response.status_code == 403
        loaded = json.loads(root_span.get_tag(_constants.APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["ua0-600-56x"]

        assert asm_config._api_security_enabled

        value = root_span.get_tag(name)
        if not parse_response_body and name == "_dd.appsec.s.res.body":
            assert value is None, "response body should not be parsed with DD_API_SECURITY_PARSE_RESPONSE_BODY=false"
        else:
            assert value, name
            api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
            assert equal_with_meta(api, expected_value), name


@pytest.mark.parametrize(["enable", "rate"], [(False, 1.0), (True, 0.0)])
def test_api_security_deactivated(client, test_spans, tracer, enable, rate):
    """Test if blocking is still working as expected with api security deactivated"""

    with override_global_config(dict(_asm_enabled=True, _api_security_enabled=enable, _api_security_sample_rate=rate)):
        payload = {"key": "secret", "ids": [0, 1, 2, 3]}
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/path-params/2022/path_param/?y=0&x=1&y=xtrace",
            payload=payload,
            cookies={"secret": "a1b2c3d4e5f6"},
            content_type="application/json",
            headers={"HTTP_USER_AGENT": "dd-test-scanner-log-block"},
        )
        assert response.status_code == 403
        loaded = json.loads(root_span.get_tag(_constants.APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["ua0-600-56x"]

        assert asm_config._api_security_enabled is enable
        assert asm_config._api_security_sample_rate == rate

        for name in [
            "_dd.appsec.s.req.body",
            "_dd.appsec.s.req.headers",
            "_dd.appsec.s.req.cookies",
            "_dd.appsec.s.req.query",
            "_dd.appsec.s.req.params",
            "_dd.appsec.s.res.headers",
            "_dd.appsec.s.res.body",
        ]:
            value = root_span.get_tag(name)
            assert value is None, name
