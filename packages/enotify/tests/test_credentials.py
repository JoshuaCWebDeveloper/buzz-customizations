import unittest

from enotify.credentials import CredentialReference, CredentialResolver, CredentialUnavailable


class CredentialTests(unittest.TestCase):
    def test_service_reference_is_structured_and_allowlisted(self):
        reference = CredentialReference.from_mapping({"scope": "service", "name": "github"})
        self.assertEqual(CredentialResolver.validate(reference, ("github",)).mapping(), {"scope": "service", "name": "github"})
        with self.assertRaisesRegex(ValueError, "unsupported credential_ref.name"):
            CredentialResolver.validate(CredentialReference.from_mapping({"scope": "service", "name": "other"}), ("github",))
        with self.assertRaises(ValueError):
            CredentialReference.from_mapping({"scope": "service", "name": "Bearer raw-token"})

    def test_github_token_precedes_gh_token_and_empty_values_are_skipped(self):
        reference = CredentialReference.from_mapping({"scope": "service", "name": "github"})
        self.assertEqual(
            CredentialResolver({"GITHUB_TOKEN": "primary", "GH_TOKEN": "fallback"}).resolve(reference, ("GITHUB_TOKEN", "GH_TOKEN")),
            "primary",
        )
        self.assertEqual(
            CredentialResolver({"GITHUB_TOKEN": "", "GH_TOKEN": "fallback"}).resolve(reference, ("GITHUB_TOKEN", "GH_TOKEN")),
            "fallback",
        )
        with self.assertRaises(CredentialUnavailable) as error:
            CredentialResolver({"GITHUB_TOKEN": "", "GH_TOKEN": ""}).resolve(reference, ("GITHUB_TOKEN", "GH_TOKEN"))
        self.assertNotIn("primary", str(error.exception))
        self.assertNotIn("fallback", str(error.exception))

    def test_unsupported_scope_is_rejected_without_environment_lookup(self):
        with self.assertRaises(ValueError):
            CredentialReference.from_mapping({"scope": "user", "name": "github"})
