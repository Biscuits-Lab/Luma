"""Release safety regressions. All network and gh actions are mocked."""
from unittest import TestCase, main
from unittest.mock import patch
import release_gate as gate

SHA = "a" * 40
OTHER = "b" * 40
REPO = "Biscuits-Lab/Luma"


def ref(sha=SHA, kind="commit"):
    return {"object": {"type": kind, "sha": sha}}


class ReleaseTagTests(TestCase):
    @patch.object(gate.subprocess, "run")
    @patch.object(gate, "api", return_value=None)
    def test_absent_tag_read_only_check_does_not_create(self, api, run):
        self.assertFalse(gate.ensure_tag_matches(REPO, SHA))
        run.assert_not_called()

    @patch.object(gate.subprocess, "run")
    @patch.object(gate, "api", side_effect=[None, ref()])
    def test_missing_tag_created_at_exact_tested_commit(self, api, run):
        self.assertTrue(gate.ensure_tag_matches(REPO, SHA, create=True))
        args = run.call_args.args[0]
        self.assertIn("ref=refs/tags/v0.1.0", args)
        self.assertIn("sha=" + SHA, args)

    @patch.object(gate.subprocess, "run")
    @patch.object(gate, "api", return_value=ref(OTHER))
    def test_existing_wrong_commit_rejected_without_mutation(self, api, run):
        with self.assertRaisesRegex(RuntimeError, "does not resolve"):
            gate.ensure_tag_matches(REPO, SHA, create=True)
        run.assert_not_called()

    @patch.object(gate, "api", return_value=ref())
    def test_matching_lightweight_tag(self, api):
        self.assertTrue(gate.ensure_tag_matches(REPO, SHA))

    @patch.object(gate, "api", side_effect=[ref(OTHER, "tag"), ref()])
    def test_matching_annotated_tag_is_peeled(self, api):
        self.assertTrue(gate.ensure_tag_matches(REPO, SHA))
        self.assertEqual(api.call_args.args[0], f"repos/{REPO}/git/tags/{OTHER}")

    @patch.object(gate, "api", side_effect=[ref("c" * 40, "tag"), ref(OTHER)])
    def test_annotated_tag_to_wrong_commit_rejected(self, api):
        with self.assertRaisesRegex(RuntimeError, "does not resolve"):
            gate.ensure_tag_matches(REPO, SHA)

    @patch.object(gate, "api", return_value=ref(OTHER, "tag"))
    def test_tag_cycle_rejected(self, api):
        with self.assertRaisesRegex(RuntimeError, "chain"):
            gate.ensure_tag_matches(REPO, SHA)

    @patch.object(gate, "api", return_value=ref(SHA, "tree"))
    def test_noncommit_reference_rejected(self, api):
        with self.assertRaisesRegex(RuntimeError, "does not resolve"):
            gate.ensure_tag_matches(REPO, SHA)

    @patch.object(gate.subprocess, "run")
    @patch.object(gate, "api", side_effect=[None, None])
    def test_created_tag_must_be_verified(self, api, run):
        with self.assertRaisesRegex(RuntimeError, "could not be verified"):
            gate.ensure_tag_matches(REPO, SHA, create=True)

    @patch.object(gate, "api", side_effect=[ref(OTHER, "tag"), None])
    def test_unresolvable_annotated_tag_rejected(self, api):
        with self.assertRaisesRegex(RuntimeError, "could not be resolved"):
            gate.ensure_tag_matches(REPO, SHA)

    def test_published_incomplete_release_is_not_overwritten(self):
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            gate.published_complete({"draft": False, "assets": []})

    def test_published_complete_release_is_idempotent(self):
        self.assertTrue(gate.published_complete({"draft": False, "assets": [{"name": a} for a in gate.ASSETS]}))


if __name__ == "__main__":
    main()
