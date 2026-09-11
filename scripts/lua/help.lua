-- Command: help
-- Trigger: ~help

function on_command(event, api)
  return api.help_card(event.is_admin == true)
end
