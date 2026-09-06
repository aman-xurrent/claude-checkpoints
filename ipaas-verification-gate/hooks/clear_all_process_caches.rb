# Finding 1 + finding 2 (spec-level variant). The connector cache clear breaks
# solution_loader_degrade_spec, which assumes a warm cache; prefer the app-level key fix.
RSpec.configure do |config|
  config.before(:each) do
    IPaaS::Encryption::SystemKeyProvider.memcache.clear
    IPaaS::Encryption::IntermediateKeyProvider.memcache.clear
    ConnectorLoader.connector_cache.clear
  end
end
