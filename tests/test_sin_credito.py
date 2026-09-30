"""Sin crédito en Anthropic, el robot para de una y deja aviso (no 231 intentos)."""
from src.extractor import es_error_de_cuenta


def test_reconoce_el_error_de_saldo():
    e = RuntimeError("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
                     "'message': 'Your credit balance is too low to access the Anthropic API.'}}")
    assert es_error_de_cuenta(e)
    assert es_error_de_cuenta(RuntimeError("Error code: 401 - authentication_error"))


def test_un_error_normal_no_es_de_cuenta():
    assert not es_error_de_cuenta(RuntimeError("Expecting value: line 1 column 1"))
    assert not es_error_de_cuenta(RuntimeError("Error code: 529 - overloaded"))
