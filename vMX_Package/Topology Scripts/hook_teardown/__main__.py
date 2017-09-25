import threading
import traceback

from cloudshell.core.logger.qs_logger import get_qs_logger
from cloudshell.workflow.orchestration.sandbox import Sandbox
from cloudshell.workflow.orchestration.teardown.default_teardown_orchestrator import DefaultTeardownWorkflow

sandbox1 = Sandbox()

DefaultTeardownWorkflow().register(sandbox1)


def f(sandbox2, hookpattern):
    api = sandbox2.automation_api
    resid = sandbox2.id

    logger = get_qs_logger(log_group=resid, log_file_prefix='setup_teardown_hooks')

    def notify(s):
        logger.info(s)
        api.WriteMessageToReservationOutput(resid, s)

    logger.info('enter f %s' % hookpattern)

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
                already.add(target)
                try:
                    if kind == 'SANDBOX':
                        o = api.ExecuteEnvironmentCommand(resid, funcname, [], True).Output
                    else:
                        o = api.ExecuteCommand(resid, target, kind, funcname, [], True).Output
                    if o:
                        notify('Hook %s.%s completed with output: %s' % (target, funcname, o))
                    else:
                        notify('Hook %s.%s completed' % (target, funcname))
                except:
                    tb = traceback.format_exc()
                    notify('Hook %s.%s threw exception: %s' % (target, funcname, tb))
                logger.info('exiting thread %s %s %s' % (kind, target, funcname))

            th = threading.Thread(target=g,
                                  name='%s.%s.%s' % (kind0, target0, funcname0),
                                  args=(kind0, target0, funcname0))
            threads.append(th)
            th.start()

        for th in threads:
            th.join()

    logger.info('exit f %s' % hookpattern)


sandbox1.workflow.before_teardown_started(f, 'orch_hook_pre_teardown')
sandbox1.workflow.add_to_teardown(f, 'orch_hook_during_teardown')

sandbox1.execute_teardown()

f(sandbox1, 'orch_hook_post_teardown')
