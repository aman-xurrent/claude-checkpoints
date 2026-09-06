class Fixture
  def method_missing(name, *args)                 # MATCH C
    super
  end

  def respond_to_missing?(name, include_private = false) # MATCH C
    true
  end

  def a(x)
    send(x)                                       # MATCH A (identifier)
  end

  def b(name)
    public_send("#{name}!")                       # MATCH A (interpolated string)
  end

  def c(key, value)
    send(:"#{key}=", value)                       # MATCH A (interpolated symbol)
  end

  def d(n)
    Object.const_get(n)                           # MATCH A
  end

  def e(k)
    "#{k}Service".constantize                     # MATCH B
  end

  def f(k)
    k.constantize                                 # MATCH B
  end

  def g(n)
    define_method(n) { 1 }                        # MATCH A
  end

  def h
    send(:literal)                                # no match
    public_send("literal")                        # no match
    define_method(:fixed) { 1 }                   # no match
    "Literal".constantize                         # no match
    try(:literal)                                 # no match
  end
end
