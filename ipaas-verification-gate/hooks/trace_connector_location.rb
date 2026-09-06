# Diagnostic: log the example after which the debug connector's source file changes.
RSpec.configure do |config|
  config.after(:each) do |example|
    connector = begin
      IPaaS::Connector::Connector.default_connector(SpecGitHelper::DEBUG_CONNECTOR_UUID)
    rescue StandardError
      nil
    end
    proc = connector&.triggers&.first&.instance_variable_get(:@parse) ||
           connector&.actions&.first&.instance_variable_get(:@run)
    location = proc&.source_location&.first
    if location != $gate_last_location
      File.open(ENV.fetch('GATE_TRACE_LOG', '/tmp/gate_debug_connector_location.log'), 'a') do |f|
        f.puts "#{example.id} | #{example.full_description[0, 90]} | #{location}"
      end
      $gate_last_location = location
    end
  end
end
