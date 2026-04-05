"""Tests for Phase 4 clarifier sub-modules extracted from OmniCoreService.

Validates that each clarifier sub-module is importable, accepts a
ServiceContext, exposes expected methods, and that ClarifierService
correctly composes its sub-modules.
"""

import unittest
from server.services.service_context import ServiceContext


def _ctx() -> ServiceContext:
    return ServiceContext()


# -- Import tests ------------------------------------------------------------

class TestClarifierImports(unittest.TestCase):
    def test_session_manager_importable(self):
        from server.services.clarifier.session_manager import SessionManager
        self.assertIsNotNone(SessionManager)

    def test_response_processor_importable(self):
        from server.services.clarifier.response_processor import ResponseProcessor
        self.assertIsNotNone(ResponseProcessor)

    def test_question_generator_importable(self):
        from server.services.clarifier.question_generator import QuestionGenerator
        self.assertIsNotNone(QuestionGenerator)

    def test_clarifier_service_importable(self):
        from server.services.clarifier import ClarifierService
        self.assertIsNotNone(ClarifierService)

    def test_clarifier_init_reexports(self):
        import server.services.clarifier as pkg
        self.assertTrue(hasattr(pkg, "ClarifierService"))


# -- Construction tests ------------------------------------------------------

class TestClarifierConstruction(unittest.TestCase):
    def setUp(self):
        self.ctx = _ctx()

    def test_session_manager(self):
        from server.services.clarifier.session_manager import SessionManager
        self.assertIsNotNone(SessionManager(self.ctx))

    def test_response_processor(self):
        from server.services.clarifier.response_processor import ResponseProcessor
        self.assertIsNotNone(ResponseProcessor(self.ctx))

    def test_question_generator(self):
        from server.services.clarifier.question_generator import QuestionGenerator
        self.assertIsNotNone(QuestionGenerator(self.ctx))

    def test_clarifier_service(self):
        from server.services.clarifier import ClarifierService
        self.assertIsNotNone(ClarifierService(self.ctx))


# -- Composition test --------------------------------------------------------

class TestClarifierComposition(unittest.TestCase):
    def test_has_sub_modules(self):
        from server.services.clarifier import ClarifierService
        svc = ClarifierService(_ctx())
        for attr in ("_questions", "_responses", "_sessions"):
            with self.subTest(attr=attr):
                self.assertTrue(
                    hasattr(svc, attr),
                    f"ClarifierService missing attribute: {attr}",
                )


# -- Method presence tests ---------------------------------------------------

class TestSessionManagerMethods(unittest.TestCase):
    def setUp(self):
        from server.services.clarifier.session_manager import SessionManager
        self.svc = SessionManager(_ctx())

    def test_methods(self):
        for name in ("run_clarifier", "cleanup_expired_clarification_sessions"):
            with self.subTest(method=name):
                self.assertTrue(hasattr(self.svc, name),
                                f"SessionManager missing: {name}")


class TestResponseProcessorMethods(unittest.TestCase):
    def setUp(self):
        from server.services.clarifier.response_processor import ResponseProcessor
        self.svc = ResponseProcessor(_ctx())

    def test_methods(self):
        for name in ("submit_clarification_response", "generate_clarified_requirements",
                      "categorize_answer"):
            with self.subTest(method=name):
                self.assertTrue(hasattr(self.svc, name),
                                f"ResponseProcessor missing: {name}")


class TestQuestionGeneratorMethods(unittest.TestCase):
    def setUp(self):
        from server.services.clarifier.question_generator import QuestionGenerator
        self.svc = QuestionGenerator(_ctx())

    def test_methods(self):
        for name in ("generate_clarification_questions",):
            with self.subTest(method=name):
                self.assertTrue(hasattr(self.svc, name),
                                f"QuestionGenerator missing: {name}")


class TestClarifierServiceMethods(unittest.TestCase):
    def setUp(self):
        from server.services.clarifier import ClarifierService
        self.svc = ClarifierService(_ctx())

    def test_delegates_exist(self):
        for name in ("run_clarifier", "submit_clarification_response",
                      "generate_clarification_questions",
                      "generate_clarified_requirements",
                      "cleanup_expired_clarification_sessions"):
            with self.subTest(method=name):
                self.assertTrue(hasattr(self.svc, name),
                                f"ClarifierService missing: {name}")


if __name__ == "__main__":
    unittest.main()
