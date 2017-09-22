from cloudshell.helpers.scripts import cloudshell_scripts_helpers as helpers

todelete = helpers.get_resource_context_details().attributes['Resources to Delete'].split(',')
helpers.get_api_session().DeleteResources(todelete)

