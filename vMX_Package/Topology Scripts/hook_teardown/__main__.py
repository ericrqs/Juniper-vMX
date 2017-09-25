from cloudshell.workflow.orchestration.sandbox import Sandbox
from cloudshell.workflow.orchestration.teardown.default_teardown_orchestrator import DefaultTeardownWorkflow

from hook_handler import handler
# searches resources and services in the reservation for functions matching a pattern and executes them in parallel

sandbox1 = Sandbox()

DefaultTeardownWorkflow().register(sandbox1)

sandbox1.workflow.before_teardown_started(handler, 'orch_hook_pre_teardown')
sandbox1.workflow.add_to_teardown(handler, 'orch_hook_during_teardown')

sandbox1.execute_teardown()

handler(sandbox1, 'orch_hook_post_teardown')
