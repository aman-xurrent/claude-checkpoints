# frozen_string_literal: true

# The removable units of the added lines of Ruby files, for the gate's per-unit proof.
#
# Input on stdin:  {"files": [{"path": "/abs/file.rb", "added_lines": [3, 4, 9]}]}
# Output on stdout: {"units": [...], "unparsed": [paths]}. A unit is listed only when its removal still
# parses. A file that is not valid UTF-8 or does not parse is listed as unparsed, never skipped in silence.

require "json"
require "prism"
require "set"

DECLARATIONS = [Prism::DefNode, Prism::ClassNode, Prism::ModuleNode, Prism::SingletonClassNode,
                Prism::ConstantWriteNode, Prism::ConstantPathWriteNode, Prism::LocalVariableWriteNode,
                Prism::MultiWriteNode, Prism::AliasMethodNode].freeze
DECLARING_CALLS = %w[private protected public module_function private_constant private_class_method include extend
                     prepend require require_relative attr_reader attr_writer attr_accessor].freeze
CONDITIONALS = [Prism::IfNode, Prism::UnlessNode].freeze
ENTRY_PARENTS = [Prism::HashNode, Prism::KeywordHashNode, Prism::ArrayNode].freeze
SCOPES = [Prism::DefNode, Prism::ClassNode, Prism::ModuleNode].freeze
# Code inside a log message's interpolation, `"#{e.class}"`, only shapes the text. Removing it was reported as a
# survivor on PR 1111, but nothing could test a log line's wording. An interpolation that builds a query or a
# method name is behaviour, and is judged.
INTERPOLATIONS = [Prism::EmbeddedStatementsNode, Prism::InterpolatedStringNode, Prism::InterpolatedSymbolNode].freeze
LOG_CALLS = %w[debug info warn error fatal log puts print p].freeze
# A log call sits on no receiver (`puts`, `p`), or on a logger: `logger`, `LOGGER`, `@logger`, `Rails.logger`,
# or a chain that ends in one (`Rails.logger.tagged(t)`).
LOGGER_RECEIVER = /(?:\A|\.)@?log(?:ger)?\z/i
# A heredoc's body sits after the line that opens it, outside the node, so removing the node leaves the body
# behind as code that still parses and then fails to run, which reads as a kill.
HEREDOC_OPENER = /<<[~-]?(["'`]?)[A-Za-z_]\w*\1/

def line_of(source, offset)
  source.byteslice(0, offset).count("\n") + 1
end

def declaration?(node)
  return true if DECLARATIONS.any? { |kind| node.is_a?(kind) }

  node.is_a?(Prism::CallNode) && node.receiver.nil? && DECLARING_CALLS.include?(node.name.to_s)
end

def whole(node)
  [[node.location.start_offset, node.location.end_offset]]
end

# An entry goes with one separator: the one after it, or the one before it when it is last.
def entry_spans(node, siblings)
  index = siblings.index { |each| each.equal?(node) }
  spans = []
  spans << [node.location.start_offset, siblings[index + 1].location.start_offset] if siblings[index + 1]
  spans << [siblings[index - 1].location.end_offset, node.location.end_offset] if index.positive?
  spans + whole(node)
end

def statement_kind(node)
  CONDITIONALS.any? { |each| node.is_a?(each) } ? "guard" : "statement"
end

def candidates_at(node, parent)
  found = []
  if parent.is_a?(Prism::StatementsNode) && !declaration?(node)
    found << { kind: statement_kind(node), spans: whole(node), replacement: "" }
  elsif ENTRY_PARENTS.any? { |each| parent.is_a?(each) }
    found << { kind: "entry", spans: entry_spans(node, parent.elements), replacement: "" }
  end
  found << { kind: "fallback", spans: whole(node), replacement: node.left.slice } if node.is_a?(Prism::OrNode)
  found << { kind: "guard", spans: whole(node), replacement: node.right.slice } if node.is_a?(Prism::AndNode)
  found
end

# The definition a unit sits in, `Presenter#react_props`, so two identical units in two methods are two units.
def scope_of(ancestors)
  ancestors.select { |node| SCOPES.any? { |kind| node.is_a?(kind) } }.map { |node| node.name.to_s }.join("#")
end

# A log call is one on a logger (`logger`, `Rails.logger`, `@logger`) or on nothing (`puts`, `p`); a domain object's
# `result.error(...)` is not.
def log_call?(node)
  return false unless node.is_a?(Prism::CallNode) && LOG_CALLS.include?(node.name.to_s)

  receiver = node.receiver
  receiver.nil? || receiver.slice.match?(LOGGER_RECEIVER)
end

# Only an interpolation inside the log call's own arguments or its block is log text: `Solution.where("... #{x}").p(1)`
# builds a query in the receiver, while `logger.debug { "value #{x}" }` is a lazy log message.
def inside_log_text?(ancestors)
  ancestors.each_with_index.any? do |ancestor, index|
    next false unless log_call?(ancestor)

    held = [ancestor.arguments, ancestor.block].compact
    held.any? { |part| ancestors[index + 1].equal?(part) } &&
      ancestors[(index + 1)..].any? { |inner| INTERPOLATIONS.any? { |kind| inner.is_a?(kind) } }
  end
end

def statement_line(source, node, ancestors)
  chain = ancestors + [node]
  index = chain.rindex { |each| each.is_a?(Prism::StatementsNode) }
  holder = index && chain[index + 1] ? chain[index + 1] : node
  line_of(source, holder.location.start_offset)
end

# A unit inside a bigger one on the same line is the sharper question, so the bigger one goes,
# except a guard: deleting a guard clause asks something its parts do not.
def innermost(units)
  units.reject do |outer|
    next false if outer[:kind] == "guard"

    units.any? do |inner|
      !inner.equal?(outer) && inner[:start_line] == outer[:start_line] &&
        inner[:start] >= outer[:start] && inner[:end] <= outer[:end] &&
        (inner[:end] - inner[:start]) < (outer[:end] - outer[:start])
    end
  end
end

def parses?(text)
  Prism.parse(text).success?
end

def units_for(path, added_lines, unparsed)
  source = File.binread(path).force_encoding(Encoding::UTF_8)
  tree = source.valid_encoding? ? Prism.parse(source) : nil
  unless tree&.success?
    unparsed << path
    return []
  end

  added = added_lines.to_set
  units = []
  walk = lambda do |node, ancestors|
    start_line = line_of(source, node.location.start_offset)
    if added.include?(start_line) && !inside_log_text?(ancestors)
      candidates_at(node, ancestors.last).each do |candidate|
        span = candidate[:spans].find do |from, to|
          parses?(source.byteslice(0, from) + candidate[:replacement] + source.byteslice(to, source.bytesize))
        end
        next unless span

        original = source.byteslice(span[0], span[1] - span[0])
        next if original.match?(HEREDOC_OPENER)

        units << { path: path, kind: candidate[:kind], start: span[0], end: span[1], scope: scope_of(ancestors),
                   replacement: candidate[:replacement], original: original, start_line: start_line,
                   end_line: line_of(source, node.location.end_offset),
                   anchor_line: statement_line(source, node, ancestors) }
      end
    end
    node.compact_child_nodes.each { |child| walk.call(child, ancestors + [node]) }
  end
  walk.call(tree.value, [])
  innermost(units.uniq { |unit| [unit[:start], unit[:end], unit[:replacement]] })
end

request = JSON.parse($stdin.read)
unparsed = []
units = request.fetch("files").flat_map { |file| units_for(file.fetch("path"), file.fetch("added_lines"), unparsed) }
puts JSON.generate({ units: units, unparsed: unparsed })
