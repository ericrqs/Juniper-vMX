from cloudshell.api.cloudshell_api import CloudShellAPISession
api = CloudShellAPISession('localhost', 'admin', 'admin', 'Global')

for r in api.GetCurrentReservations('admin').Reservations:
    api.EndReservation(r.Id)

