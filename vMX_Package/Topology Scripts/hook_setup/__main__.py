from cloudshell.workflow.orchestration.sandbox import Sandbox
from cloudshell.workflow.orchestration.setup.default_setup_orchestrator import DefaultSetupWorkflow

from hook_handler import handler
# searches resources and services in the reservation for functions matching a pattern and executes them in parallel

sandbox1 = Sandbox()

DefaultSetupWorkflow().register(sandbox1)


sandbox1.workflow.add_to_preparation(handler, 'orch_hook_during_preparation')
sandbox1.workflow.on_preparation_ended(handler, 'orch_hook_post_preparation')

sandbox1.workflow.add_to_provisioning(handler, 'orch_hook_during_provisioning')
sandbox1.workflow.on_provisioning_ended(handler, 'orch_hook_post_provisioning')

sandbox1.workflow.add_to_connectivity(handler, 'orch_hook_during_connectivity')
sandbox1.workflow.on_connectivity_ended(handler, 'orch_hook_post_connectivity')

sandbox1.workflow.add_to_configuration(handler, 'orch_hook_during_configuration')
sandbox1.workflow.on_configuration_ended(handler, 'orch_hook_post_configuration')


handler(sandbox1, 'orch_hook_pre_setup')
sandbox1.execute_setup()
handler(sandbox1, 'orch_hook_post_setup')
