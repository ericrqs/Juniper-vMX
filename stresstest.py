import threading
from random import randint
from time import sleep, time

from cloudshell.api.cloudshell_api import CloudShellAPISession

toponame = 'vmx openstack 201'
nthreads = 5


def log(s):
    with open(r'c:\programdata\qualisystems\stresstest.log', 'a') as ff:
        ff.write(s + '\n')


def f(jj):
    k = randint(0, 300)
    log('%d: Sleeping %d seconds' % (jj, k))
    sleep(k)
    while True:
        t0 = time()
        fail = 'ok'
        api = CloudShellAPISession('localhost', 'admin', 'admin', 'Global')
        try:
            id = api.CreateImmediateTopologyReservation(reservationName='vmx%d' % jj,
                                                        topologyFullPath=toponame,
                                                        durationInMinutes=120,
                                                        owner='admin').Reservation.Id
            for _ in range(100):
                rd = api.GetReservationDetails(id).ReservationDescription

                log('%d: %s Status=%s SetupStage=%s ProvisioningStatus=%s' % (jj, id, rd.Status, rd.SetupStage, rd.ProvisioningStatus))
                if rd.ProvisioningStatus == 'Error':
                    fail = 'setup error'
                    break
                if rd.SetupStage == 'Ended':
                    break
                sleep(10)
            else:
                log('%d: Setup never finished, ending reservation %s' % (jj, id))
                fail = 'setup timeout'

            api.EndReservation(id)

            for _ in range(100):
                rd = api.GetReservationDetails(id).ReservationDescription
                if rd.Status == 'Completed':
                    log('%d: %s Status=%s' % (jj, id, rd.Status))
                    break
                sleep(10)
            else:
                log('%d: Teardown never finished %s' % (jj, id))
                fail = 'teardown fail'

            log('%d: Cycle (%s) finished in %d seconds' % (jj, str(fail), time()-t0))
        except Exception as e:
            log('Exception: %s' % str(e))
            sleep(randint(1, 30))


for i in range(nthreads):
    threading.Thread(target=f, args=(i,)).start()
