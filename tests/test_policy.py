"""Policy loading warns when fields would otherwise be silently ignored."""

import unittest
import warnings

from tempera.core.policy import Policy


class PolicyWarningTest(unittest.TestCase):
    def test_unknown_policy_and_access_fields_warn(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            Policy.from_dict({
                "forbiden_paths": [],
                "forbidden": {"forbiden_actions": []},
            })

        self.assertEqual(2, len(caught))
        self.assertIn("policy: unknown fields ignored: ['forbiden_paths']", str(caught[0].message))
        self.assertIn(
            "policy.access: unknown fields ignored: ['forbiden_actions']",
            str(caught[1].message),
        )

    def test_known_fields_do_not_warn(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            Policy.from_dict({
                "schema_version": "0.1",
                "allowed": {"targets": [], "actions": [], "resources": [], "behaviors": []},
                "forbidden": {"database_modifications": [], "event_rules": []},
                "scope": {"allowed_hosts": [], "allowed_ports": [], "allowed_schemes": []},
                "minimality": {"forbid_post_goal_requests": True},
                "behaviors": {},
                "agent_instructions": "stay in scope",
            })

        self.assertEqual([], caught)

    def test_removed_scope_field_warns_but_loads(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            policy = Policy.from_dict({"scope": {"forbidden_paths": ["/admin"]}})

        self.assertIsInstance(policy, Policy)
        self.assertEqual(1, len(caught))
        self.assertIn(
            "policy.scope: unknown fields ignored: ['forbidden_paths']",
            str(caught[0].message),
        )

    def test_minimality_defaults_preserve_methods_without_target_exemptions(self):
        policy = Policy.from_dict({})

        self.assertEqual(
            frozenset({"PUT", "DELETE", "PATCH", "POST"}),
            policy.state_changing_methods,
        )
        self.assertEqual(frozenset(), policy.exempt_paths)

    def test_minimality_fields_load_and_serialize(self):
        policy = Policy.from_dict({
            "minimality": {
                "forbid_post_goal_requests": True,
                "state_changing_methods": ["POST", "PATCH"],
                "exempt_paths": ["/safe", "/safe/"],
            },
        })

        self.assertEqual(frozenset({"POST", "PATCH"}), policy.state_changing_methods)
        self.assertEqual(frozenset({"/safe", "/safe/"}), policy.exempt_paths)
        self.assertEqual({"forbid_post_goal_requests": True,
                          "state_changing_methods": ["PATCH", "POST"],
                          "exempt_paths": ["/safe", "/safe/"]},
                         policy.to_dict()["minimality"])

    def test_top_level_r2_is_not_a_canonical_policy_key(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            policy = Policy.from_dict({"R2": {"allowed_tools": ["browser"]}})

        self.assertEqual({}, policy.roe)
        self.assertIn("policy: unknown fields ignored: ['R2']", str(caught[0].message))

    def test_top_level_r2_and_canonical_tool_policy_are_rejected_as_ambiguous(self):
        with self.assertRaisesRegex(ValueError, "ambiguous policy"):
            Policy.from_dict({
                "R2": {"allowed_tools": ["browser"]},
                "roe": {"tool_authorization": {"allowed_tools": ["http_request"]}},
            })


if __name__ == "__main__":
    unittest.main()
