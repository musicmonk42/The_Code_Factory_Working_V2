# test_remediations.py
# Comprehensive production-grade tests for remediations.py
# Requires: pytest, pytest-asyncio, aiohttp, tenacity
# Run with: pytest test_remediations.py -v --cov=remediations

from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from aiohttp import ClientError

# Import the module to be tested
from arbiter.bug_manager.remediations import (
    MLRemediationModel, RemediationStep, RemediationPlaybook, BugFixerRegistry,
    RemediationError, MLRemediationError
)

# --- Fixtures ---

@pytest.fixture(autouse=True)
def clean_action_registry():
    """Ensures the RemediationStep action registry is clean for each test."""
    RemediationStep._action_registry.clear()
    yield
    RemediationStep._action_registry.clear()

@pytest.fixture
def mock_aiohttp_session():
    """Mocks aiohttp.ClientSession to control API responses for the ML model."""
    with patch('aiohttp.ClientSession') as mock_session_class:
        mock_session = MagicMock()
        
        # Create a proper async context manager mock
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"playbook_name": "ML_Playbook", "confidence": 0.9})
        mock_response.raise_for_status = MagicMock()
        
        # Setup context manager
        mock_context = MagicMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        
        mock_session.post.return_value = mock_context
        mock_session_class.return_value = mock_session
        yield mock_session

@pytest.fixture
async def ml_model(mock_aiohttp_session):
    """Provides an MLRemediationModel instance that is automatically closed."""
    # Create a mock settings object for the model
    mock_settings = MagicMock()
    mock_settings.ML_REMEDIATION_RETRY_ATTEMPTS = 3
    
    model = MLRemediationModel("http://fake-model-endpoint.com", settings=mock_settings)
    yield model
    await model.close()

@pytest.fixture
def bug_details():
    """Provides a sample bug details dictionary for tests."""
    return {
        "message": "Database connection timed out",
        "exception_type": "ConnectionError",
        "location": "database.connector",
        "severity": "critical",
        "signature": "db_conn_timeout_sig"
    }

# --- Test Cases ---

class TestMLRemediationModel:
    @pytest.mark.asyncio
    async def test_predict_success(self, ml_model, bug_details, mock_aiohttp_session):
        playbook, confidence = await ml_model.predict_remediation_strategy(bug_details)
        assert playbook == "ML_Playbook"
        assert confidence == 0.9
        mock_aiohttp_session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_predict_api_error_raises_custom_exception(self, ml_model, bug_details, mock_aiohttp_session):
        mock_aiohttp_session.post.side_effect = ClientError("Internal Server Error")
        with pytest.raises(MLRemediationError, match="ML prediction API failed"):
            await ml_model.predict_remediation_strategy(bug_details)
        # Just verify retry count is correct

    @pytest.mark.asyncio
    async def test_record_feedback_success(self, ml_model, bug_details, mock_aiohttp_session):
        await ml_model.record_remediation_outcome(bug_details, "TestPlaybook", "success")
        feedback_endpoint = f"{ml_model.model_endpoint}/feedback"
        mock_aiohttp_session.post.assert_called_once()
        # Verify the call was to the feedback endpoint with correct data
        call_args, call_kwargs = mock_aiohttp_session.post.call_args
        assert call_args[0] == feedback_endpoint
        assert call_kwargs["json"]["playbook_executed"] == "TestPlaybook"
        assert call_kwargs["json"]["remediation_outcome"] == "success"

class TestRemediationStep:
    @pytest.mark.asyncio
    async def test_execute_success(self, bug_details):
        mock_action = AsyncMock(return_value=True)
        RemediationStep.register_action("mock_success_action", mock_action)
        step = RemediationStep(name="TestStep", action_name="mock_success_action")
        result = await step.execute(bug_details, "TestPlaybook")
        assert result is True
        mock_action.assert_awaited_once_with(bug_details)

    @pytest.mark.asyncio
    async def test_execute_with_retries_on_failure(self, bug_details):
        # Tenacity retries on exceptions, not False return values. Test exception retry.
        mock_action = AsyncMock(side_effect=[ValueError("Fail 1"), ValueError("Fail 2"), True])
        RemediationStep.register_action("flaky_action", mock_action)
        step = RemediationStep(name="FlakyStep", action_name="flaky_action", retries=2, retry_delay_seconds=0.01)
        
        result = await step.execute(bug_details, "TestPlaybook")
        
        assert result is True
        assert mock_action.await_count == 3

    @pytest.mark.asyncio
    async def test_execute_exhausts_retries(self, bug_details):
        mock_action = AsyncMock(return_value=False)
        RemediationStep.register_action("failing_action", mock_action)
        step = RemediationStep(name="FailingStep", action_name="failing_action", retries=2, retry_delay_seconds=0.01)
        
        # Action always returns False, so it should eventually fail. Retries are for exceptions.
        result = await step.execute(bug_details, "TestPlaybook")
        
        assert result is False
        assert mock_action.await_count == 1

    @pytest.mark.asyncio
    async def test_execute_exception_exhausts_retries(self, bug_details):
        mock_action = AsyncMock(side_effect=ValueError("Action failed"))
        RemediationStep.register_action("error_action", mock_action)
        step = RemediationStep(name="ErrorStep", action_name="error_action", retries=1, retry_delay_seconds=0.01)
        
        with pytest.raises(RemediationError):
            await step.execute(bug_details, "TestPlaybook")
            
        assert mock_action.await_count == 2 # 1 initial call + 1 retry

    @pytest.mark.asyncio
    async def test_precondition_skip(self, bug_details):
        mock_action = AsyncMock()
        RemediationStep.register_action("precondition_action", mock_action)
        mock_precondition = AsyncMock(return_value=False)
        step = RemediationStep(name="PreconditionStep", action_name="precondition_action", pre_condition=mock_precondition)
        
        result = await step.execute(bug_details, "TestPlaybook")
        
        assert result is False
        mock_precondition.assert_awaited_once_with(bug_details)
        mock_action.assert_not_awaited()

class TestRemediationPlaybook:
    @pytest.mark.asyncio
    async def test_successful_run(self, bug_details):
        mock_action1 = AsyncMock(return_value=True)
        RemediationStep.register_action("success_playbook_action1", mock_action1)
        mock_action2 = AsyncMock(return_value=True)
        RemediationStep.register_action("success_playbook_action2", mock_action2)
        
        step1 = RemediationStep(name="Step1", action_name="success_playbook_action1", on_success="Step2")
        step2 = RemediationStep(name="Step2", action_name="success_playbook_action2", on_success="FINISH")
        playbook = RemediationPlaybook(name="SuccessPlaybook", steps=[step1, step2])
        
        result = await playbook.execute("test.location", bug_details)
        
        assert result is True
        mock_action1.assert_awaited_once()
        mock_action2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_with_step_failure(self, bug_details):
        mock_action_step1 = AsyncMock(return_value=False)
        RemediationStep.register_action("failure_playbook_action1", mock_action_step1)
        mock_action_step2 = AsyncMock(return_value=True)
        RemediationStep.register_action("failure_playbook_action2", mock_action_step2)
        mock_action_notify = AsyncMock(return_value=True)
        RemediationStep.register_action("failure_playbook_notify_action", mock_action_notify)

        step1 = RemediationStep(name="Step1", action_name="failure_playbook_action1", on_failure="Notify")
        step2 = RemediationStep(name="SuccessfulStep", action_name="failure_playbook_action2") # Should not be called
        step_notify = RemediationStep(name="Notify", action_name="failure_playbook_notify_action", on_success="ABORT")
        playbook = RemediationPlaybook(name="FailurePlaybook", steps=[step1, step2, step_notify])

        result = await playbook.execute("test.location", bug_details)

        assert result is False
        mock_action_step1.assert_awaited_once()
        mock_action_notify.assert_awaited_once()
        mock_action_step2.assert_not_awaited()

class TestBugFixerRegistry:
    @pytest.fixture(autouse=True)
    def clean_registry(self):
        """Ensures the registry is clean for each test."""
        BugFixerRegistry._playbooks.clear()
        BugFixerRegistry._ml_remediation_model = None
        yield

    @pytest.mark.asyncio
    async def test_rule_based_selection_priority(self, bug_details):
        # Setup mock playbooks
        RemediationStep.register_action("specific_action", AsyncMock(return_value=True))
        pb_specific = RemediationPlaybook("Specific", [RemediationStep("s1", action_name="specific_action")])
        
        RemediationStep.register_action("general_loc_action", AsyncMock(return_value=True))
        pb_general_loc = RemediationPlaybook("GeneralLoc", [RemediationStep("s2", action_name="general_loc_action")])
        
        RemediationStep.register_action("global_action", AsyncMock(return_value=True))
        pb_global = RemediationPlaybook("Global", [RemediationStep("s3", action_name="global_action")])

        BugFixerRegistry.register_playbook(pb_specific, location="database.connector", bug_signature_prefix="db_conn")
        BugFixerRegistry.register_playbook(pb_general_loc, location="database.connector", bug_signature_prefix="*")
        BugFixerRegistry.register_playbook(pb_global, location="*", bug_signature_prefix="*")

        # Mock the execute method to track which playbook was called
        with patch.object(pb_specific, 'execute', new=AsyncMock(return_value=True)) as mock_exec_spec, \
             patch.object(pb_general_loc, 'execute', new=AsyncMock(return_value=True)) as mock_exec_gen, \
             patch.object(pb_global, 'execute', new=AsyncMock(return_value=True)) as mock_exec_glob:

            # Test specific match
            await BugFixerRegistry.run_remediation("database.connector", bug_details, "db_conn_timeout_sig")
            mock_exec_spec.assert_awaited_once()
            mock_exec_gen.assert_not_awaited()
            mock_exec_glob.assert_not_awaited()

            mock_exec_spec.reset_mock()

            # Test general location match
            await BugFixerRegistry.run_remediation("database.connector", bug_details, "some_other_sig")
            mock_exec_spec.assert_not_awaited()
            mock_exec_gen.assert_awaited_once()
            mock_exec_glob.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ml_selection_override(self, bug_details, ml_model):
        RemediationStep.register_action("ml_rule_action", AsyncMock(return_value=True))
        rule_playbook = RemediationPlaybook("RulePlaybook", [RemediationStep("s1", action_name="ml_rule_action")])
        
        RemediationStep.register_action("ml_playbook_action", AsyncMock(return_value=True))
        ml_playbook = RemediationPlaybook("ML_Playbook", [RemediationStep("s_ml", action_name="ml_playbook_action")])
        
        BugFixerRegistry.register_playbook(rule_playbook, location="database.connector", bug_signature_prefix="*")
        BugFixerRegistry.register_playbook(ml_playbook, location="global", bug_signature_prefix="*") # Register the ML playbook
        BugFixerRegistry.set_ml_model(ml_model)

        with patch.object(ml_playbook, 'execute', new=AsyncMock(return_value=True)) as mock_exec_ml, \
             patch.object(rule_playbook, 'execute', new=AsyncMock(return_value=True)) as mock_exec_rule:
            
            # ML model will recommend "ML_Playbook" with high confidence
            await BugFixerRegistry.run_remediation("database.connector", bug_details, "db_conn_timeout_sig")
            
            mock_exec_ml.assert_awaited_once()
            mock_exec_rule.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_feedback_is_skipped_when_no_playbook_found(self, bug_details, ml_model):
        """Ensure feedback is not sent if no remediation was even attempted."""
        BugFixerRegistry.set_ml_model(ml_model)
        
        # Mock the prediction to return nothing
        ml_model.predict_remediation_strategy = AsyncMock(return_value=(None, 0.0))
        
        with patch.object(ml_model, 'record_remediation_outcome', new_callable=AsyncMock) as mock_feedback:
            # Run with a bug that has no matching rule-based playbook
            await BugFixerRegistry.run_remediation("unknown.location", bug_details, "unknown_sig")
            
            # Assert that feedback was NOT called
            mock_feedback.assert_not_awaited()