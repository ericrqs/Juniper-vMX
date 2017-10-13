from cloudshell.api.cloudshell_api import CloudShellAPISession
api = CloudShellAPISession('localhost', 'admin', 'admin', 'Global')

for r in api.GetCurrentReservations('admin').Reservations:
    try:
        print r.Id
        api.EndReservation(r.Id)
    except:
        print 'Failed'
