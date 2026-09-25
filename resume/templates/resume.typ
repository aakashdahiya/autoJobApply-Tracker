// Matches the source resume's format: Calibri 10pt, 0.5in/0.6in margins, 17pt
// name, 11pt bold section headings with a hairline rule, a per-role "Tech:"
// line, right-aligned dates, and a first-class Projects section.
//
// ATS rules still hold underneath it: single column, no tables, no text boxes,
// no page header or footer, contact details in the body, a real text layer,
// hyphenation off so extracted text matches the source exactly.

#let data = json(bytes(sys.inputs.at("data")))
#let id = data.identity

#let runs(rs) = {
  for r in rs {
    if r.bold { strong(r.text) } else { r.text }
  }
}

// Right-aligned when date_style is "tab" (the source format); appended inline
// when "inline", which no extractor can misread.
#let trailing(main, aside) = {
  if aside == none or aside == "" {
    main
  } else if data.date_style == "inline" {
    [#main | #aside]
  } else {
    [#main #h(1fr) #aside]
  }
}

#set document(title: data.doc_title, author: id.name)
#set page(paper: "us-letter", margin: (top: 0.5in, bottom: 0.5in, x: 0.6in))
#set text(
  font: ("Calibri", "Carlito", "Liberation Sans", "DejaVu Sans"),
  size: 10pt,
  hyphenate: false,
  lang: "en",
  region: "ca",
)
#set par(justify: false, leading: 0.68em)
#set list(marker: [•], indent: 0.3em, body-indent: 0.4em, tight: false, spacing: 4pt)

#let section(title) = block(above: 9pt, below: 4pt, width: 100%)[
  #text(size: 11pt, weight: "bold")[#upper(title)]
  #v(-0.25em)
  #line(length: 100%, stroke: 0.75pt + rgb("#444444"))
]

// --- Header --------------------------------------------------------------
#text(size: 17pt, weight: "bold")[#upper(id.name)]

#v(0.2em)
#text(size: 10pt)[#data.contact_primary.join("  |  ")]

#if data.contact_links.len() > 0 [
  #v(0.15em)
  #text(size: 10pt)[#data.contact_links.join("  |  ")]
]

#if data.work_auth_line != none [
  #v(0.15em)
  #text(size: 9.5pt)[#data.work_auth_line]
]

// --- Summary -------------------------------------------------------------
#if data.summary != none [
  #section("Summary")
  #data.summary
]

// --- Experience ----------------------------------------------------------
#if data.experience.len() > 0 [
  #section("Experience")
  #for role in data.experience [
    #block(above: 7pt, below: 2pt, width: 100%)[
      #trailing([#strong(role.title) | #role.company (#role.location)], role.dates)
    ]
    #if role.tech.len() > 0 [
      #block(above: 0pt, below: 4pt)[#strong("Tech: ")#role.tech.join(", ")]
    ]
    #block(above: 0pt, below: 0pt)[#list(..role.bullets.map(b => runs(b)))]
  ]
]

// --- Projects ------------------------------------------------------------
#if data.projects.len() > 0 [
  #section("Projects")
  #for p in data.projects [
    #block(above: 7pt, below: 2pt, width: 100%)[
      #trailing(
        if p.tagline != none [#strong(p.name) --- #p.tagline] else [#strong(p.name)],
        p.tech.join(", "),
      )
    ]
    #block(above: 0pt, below: 0pt)[
      #list(
        ..p.bullets.map(b => runs(b)),
        ..if p.link != none { ([Source code and recorded demo: #p.link],) } else { () },
      )
    ]
  ]
]

// --- Technical skills ----------------------------------------------------
#if data.skills.len() > 0 [
  #section("Technical Skills")
  #for group in data.skills [
    #block(above: 0pt, below: 3pt)[#strong(group.category + ": ")#group.items.join(", ")]
  ]
]

// --- Education -----------------------------------------------------------
#if data.education.len() > 0 [
  #section("Education")
  #for e in data.education [
    #block(above: 5pt, below: 0pt, width: 100%)[
      #trailing([#strong(e.credential) | #e.institution, #e.location], e.dates)
    ]
    #if e.note != none [
      #block(above: 1pt, below: 0pt)[#e.note]
    ]
  ]
]
