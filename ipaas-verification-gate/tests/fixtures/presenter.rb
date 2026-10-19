class SolutionPresenter
  private

  def react_props
    {
      name: solution.name,
      icon_url: icon_url,
    }
  end

  def event_summary(event, solutions)
    organizer = event['organizer'] || {}
    return {} if solutions.empty?
    return nil unless User.exists?(id: event[:id])
    allow_specialist :show
    log(organizer)
  end
end
