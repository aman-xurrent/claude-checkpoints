# Finding 1: class-level key caches outlive the per-example transaction rollback.
RSpec.configure do |config|
  config.before(:each) do
    IPaaS::Encryption::SystemKeyProvider.memcache.clear
    IPaaS::Encryption::IntermediateKeyProvider.memcache.clear
  end
end
