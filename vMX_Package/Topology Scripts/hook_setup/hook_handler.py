import threading
import traceback

from cloudshell.core.logger.qs_logger import get_qs_logger

from constraints import constraint_sorted


def handler(sandbox, hookpattern):
    """
    To be registered as a custom Setup or Teardown function

    Executes hook functions as much as possible in parallel, serializing according to constraints specified
    in the alias or Constraints attributes of any Hook Constraints services in the reservation

    :param sandbox: cloudshell.workflow.orchestration.sandbox.Sandbox
    :param hookpattern: pattern to locate matching functions on resources and services this handler should execute
    :return:
    """
    api = sandbox.automation_api
    resid = sandbox.id

    logger = get_qs_logger(log_group=resid, log_file_prefix='setup_teardown_hooks')

    def notify(s):
        logger.info(s)
        api.WriteMessageToReservationOutput(resid, s)

    logger.info('enter handler %s' % hookpattern)

    already = set()
    passno = 1
    while True:
        # recheck reservation every time hooks in this phase were run, in case a hook changed the reservation
        logger.info('get reservation details')
        rd = api.GetReservationDetails(sandbox.id).ReservationDescription

        constraints = []

        for svc in rd.Services:
            if svc.ServiceName == 'Hook Constraints':
                cc = []
                if 'before' in svc.Alias or 'after' in svc.Alias:
                    cc.append(svc.Alias)
                for a in svc.Attributes:
                    if 'constraint' in a.Name.lower():
                        cc += [x for x in a.Value.split(';') if x]
                for c in cc:
                    constraint_error_message = 'Uninterpretable constraint %s. Expected format: TARGET_SPEC TIME TARGET_SPEC;TARGET_SPEC TIME TARGET_SPEC;...  -- TIME::=before|after  TARGET_SPEC::=TS | TS,TARGET_SPEC  TS::=family(FAMILY_REGEX) | model(MODEL_REGEX) | name(NAME_REGEX) | funcname(FUNC_REGEX) | CATCHALL_REGEX (all case insensitive) -- Examples: family(server) after model(cisco),name(fabric);family(power),funcname(pre) before .*' % c
                    if 'before' in c or 'after' in c:
                        logger.warn(constraint_error_message)
                        continue
                    if 'before' in c:
                        a, b = c.split('before')
                    else:
                        b, a = c.split('after')
                    a = a.strip()
                    b = b.strip()
                    constraints.append((a, b))

        to_run = []

        # requires Server\customer.config: <add key="AllowConcurrentTopologyScriptCommands" value="True"/>
        for c in api.GetEnvironmentCommands(resid).Commands:
            if hookpattern in c.Name:
                logger.info('sandbox hook found: %s' % c.Name)
                if 'SANDBOX.' + c.Name in already:
                    logger.info('sandbox hook already called: %s' % c.Name)
                    continue
                already.add('SANDBOX.' + c.Name)
                to_run.append(('SANDBOX', 'SANDBOX', 'SANDBOX', 'SANDBOX', c.Name))

        for svc in rd.Services:
            logger.info('service %s' % svc.Alias)
            for c in api.GetServiceCommands(svc.ServiceName).Commands:
                if hookpattern in c.Name:
                    logger.info('service %s hook %s found' % (svc.Alias, c.Name))
                    if svc.Alias + '.' + c.Name in already:
                        logger.info('service %s hook %s already called' % (svc.Alias, c.Name))
                        continue
                    already.add(svc.Alias + '.' + c.Name)
                    to_run.append(('Service', 'SERVICE_FAMILY', svc.ServiceName, svc.Alias, c.Name))

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
                    to_run.append(('Resource', r.ResourceFamilyName, r.ResourceModelName, r.Name, c.Name))

        if not to_run:
            break

        nodechains = constraint_sorted(to_run, constraints)

        threads = []

        for i, nodechain in enumerate(nodechains):
            def g(chain):
                logger.info('entering thread %s' % chain)
                for kind, family, model, target, funcname in chain:
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
                logger.info('exiting thread %s' % chain)

            th = threading.Thread(target=g,
                                  name='pass%dchain%d' % (passno, i),
                                  args=(nodechain,))
            threads.append(th)
            th.start()

        for th in threads:
            th.join()
        passno += 1
    logger.info('exit handler %s' % hookpattern)
