import threading
from random import randint
from time import sleep, time

from cloudshell.api.cloudshell_api import CloudShellAPISession


def f(jj):
    k = randint(0, 300)
    print '%d: Sleeping %d seconds' % (jj, k)
    sleep(k)
    while True:
        t0 = time()
        fail = 'ok'
        api = CloudShellAPISession('localhost', 'admin', 'admin', 'Global')
        try:
            id = api.CreateImmediateTopologyReservation(reservationName='vmx%d' % jj,
                                                        topologyFullPath='vmx vsphere 202',
                                                        durationInMinutes=120,
                                                        owner='admin').Reservation.Id
            for _ in range(70):
                rd = api.GetReservationDetails(id).ReservationDescription

                if rd.SetupStage == 'Ended' or rd.ProvisioningStatus == 'Error':
                    print '%d: %s Status=%s SetupStage=%s ProvisioningStatus=%s' % (jj, id, rd.Status, rd.SetupStage, rd.ProvisioningStatus)
                    break
                sleep(10)
            else:
                print '%d: Setup never finished, ending reservation %s' % (jj, id)
                fail = 'setup fail'

            api.EndReservation(id)

            for _ in range(100):
                rd = api.GetReservationDetails(id).ReservationDescription
                if rd.Status == 'Completed':
                    print '%d: %s Status=%s' % (jj, id, rd.Status)
                    break
                sleep(10)
            else:
                print '%d: Teardown never finished %s' % (jj, id)
                fail = 'teardown fail'

            print '%d: Cycle (%s) finished in %d seconds' % (jj, str(fail), time()-t0)
        except Exception as e:
            print 'Exception: %s' % str(e)
            sleep(randint(1, 30))


for i in range(15):
    threading.Thread(target=f, args=(i,)).start()
