# frozen_string_literal: true

# Loaded with `rspec -r`. After the suite, writes the raw line counts of every file under
# GATE_COVERAGE_ROOT to GATE_COVERAGE_OUT. Raw, because the project's SimpleCov filters drop
# directories (`lib/connectors`) whose lines the per-unit proof still has to judge.

require "coverage"
require "json"

Coverage.start(lines: true) unless Coverage.running?

RSpec.configure do |config|
  config.after(:suite) do
    # The separator keeps a sibling such as `.../gate2/x.rb` out of `.../gate`.
    root = File.join(ENV.fetch("GATE_COVERAGE_ROOT"), "")
    counts = Coverage.peek_result.each_with_object({}) do |(path, data), kept|
      next unless path.start_with?(root)

      kept[path] = data.is_a?(Hash) ? data[:lines] : data
    end
    File.write(ENV.fetch("GATE_COVERAGE_OUT"), JSON.generate(counts))
  end
end
