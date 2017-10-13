import threading
import traceback
from time import time

from cloudshell.core.logger.qs_logger import get_qs_logger
from cloudshell.workflow.orchestration.sandbox import Sandbox
from cloudshell.workflow.orchestration.setup.default_setup_orchestrator import DefaultSetupWorkflow

sandbox1 = Sandbox()

DefaultSetupWorkflow().register(sandbox1)


def f(sandbox2, hookpattern):
    api = sandbox2.automation_api
    resid = sandbox2.id

    logger = get_qs_logger(log_group=resid, log_file_prefix='setup_teardown_hooks')

    def notify(s):
        logger.info(s)
        api.WriteMessageToReservationOutput(resid, s)

    logger.info('enter f %s' % hookpattern)

    errors = []
    already = set()
    while True:
        # recheck reservation every time hooks in this phase were run, in case a hook changed the reservation
        logger.info('get reservation details')
        rd = api.GetReservationDetails(sandbox2.id).ReservationDescription

        to_run = []

        # requires Server\customer.config: <add key="AllowConcurrentTopologyScriptCommands" value="True"/>
        for c in api.GetEnvironmentCommands(resid).Commands:
            if hookpattern in c.Name:
                logger.info('sandbox hook found: %s' % c.Name)
                if 'SANDBOX.' + c.Name in already:
                    logger.info('sandbox hook already called: %s' % c.Name)
                    continue
                already.add('SANDBOX.' + c.Name)
                to_run.append(('SANDBOX', 'SANDBOX', c.Name))

        for svc in rd.Services:
            logger.info('service %s' % svc.Alias)
            for c in api.GetServiceCommands(svc.ServiceName).Commands:
                if hookpattern in c.Name:
                    logger.info('service %s hook %s found' % (svc.Alias, c.Name))
                    if svc.Alias + '.' + c.Name in already:
                        logger.info('service %s hook %s already called' % (svc.Alias, c.Name))
                        continue
                    already.add(svc.Alias + '.' + c.Name)
                    to_run.append(('Service', svc.Alias, c.Name))

        for r in rd.Resources:
            if '/' in r.Name:  # assuming functions can only exist on root resources
                continue
            logger.info('resource %s' % r.Name)
            for c in api.GetResourceCommands(r.Name).Commands:
                if hookpattern in c.Name:
                    logger.info('resource %s hook found: %s' % (r.Name, c.Name))
                    if r.Name + '.' + c.Name in already:
                        logger.info('resource %s hook already called: %s' % (r.Name, c.Name))
                        continue
                    already.add(r.Name + '.' + c.Name)
                    to_run.append(('Resource', r.Name, c.Name))

        if not to_run:
            break

        threads = []
        for kind0, target0, funcname0 in to_run:
            def g(kind, target, funcname):
                logger.info('entering thread %s %s %s' % (kind, target, funcname))
                t0 = time()
                already.add(target)
                try:
                    if kind == 'SANDBOX':
                        o = api.ExecuteEnvironmentCommand(resid, funcname, [], True).Output
                    else:
                        o = api.ExecuteCommand(resid, target, kind, funcname, [], True).Output
                    if o:
                        notify('Hook %s.%s completed in %d seconds with output: %s' % (target, funcname, time()-t0, o))
                    else:
                        notify('Hook %s.%s completed in %d seconds' % (target, funcname, time()-t0))
                except:
                    tb = traceback.format_exc()
                    notify('Hook %s.%s threw exception after %d seconds: %s' % (target, funcname, time()-t0, tb))
                    errors.append(tb)
                logger.info('exiting thread %s %s %s' % (kind, target, funcname))

            th = threading.Thread(target=g,
                                  name='%s.%s.%s' % (kind0, target0, funcname0),
                                  args=(kind0, target0, funcname0))
            threads.append(th)
            th.start()

        for th in threads:
            th.join()

    if errors:
        logger.info('Errors: %s' % errors)
        raise Exception('Errors: %s' % errors)
    logger.info('exit f %s' % hookpattern)


sandbox1.workflow.add_to_preparation(f, 'orch_hook_during_preparation')
sandbox1.workflow.on_preparation_ended(f, 'orch_hook_post_preparation')

sandbox1.workflow.add_to_provisioning(f, 'orch_hook_during_provisioning')
sandbox1.workflow.on_provisioning_ended(f, 'orch_hook_post_provisioning')

sandbox1.workflow.add_to_connectivity(f, 'orch_hook_during_connectivity')
sandbox1.workflow.on_connectivity_ended(f, 'orch_hook_post_connectivity')

sandbox1.workflow.add_to_configuration(f, 'orch_hook_during_configuration')
sandbox1.workflow.on_configuration_ended(f, 'orch_hook_post_configuration')


f(sandbox1, 'orch_hook_pre_setup')
sandbox1.execute_setup()
f(sandbox1, 'orch_hook_post_setup')
