// ATS-safe resume template. The rules it exists to enforce (docs/ARCHITECTURE.md §4):
// single column, no tables or text boxes, no page header or footer, contact
// details in the body, standard section names, a real text layer, no images.
// Hyphenation is off so that extracted text matches the source exactly.

#let data = json(bytes(sys.inputs.at("data")))
#let id = data.identity

#set document(title: data.at("doc_title"), author: id.name)
#set page(paper: "us-letter", margin: (x: 0.7in, y: 0.6in))
#set text(
  font: ("Liberation Sans", "Arial", "Helvetica", "DejaVu Sans"),
  size: 10pt,
  hyphenate: false,
  lang: "en",
  region: "ca",
)
#set par(justify: false, leading: 0.55em)
#set list(marker: [•], indent: 0.4em, body-indent: 0.45em, spacing: 0.55em)

#let section(title) = block(above: 1.1em, below: 0.6em)[
  #text(size: 10.5pt, weight: "bold", tracking: 0.04em)[#upper(title)]
  #v(-0.5em)
  #line(length: 100%, stroke: 0.6pt + rgb("#444444"))
]

// --- Header: name, then contact details as plain body text ---------------
#text(size: 17pt, weight: "bold")[#id.name]

#v(0.25em)
#text(size: 9.5pt)[#data.contact.join("  |  ")]

#if data.at("work_auth_line") != none [
  #v(0.3em)
  #text(size: 9.5pt)[#data.work_auth_line]
]

// --- Summary -------------------------------------------------------------
#if data.at("summary") != none [
  #section("Summary")
  #data.summary
]

// --- Skills --------------------------------------------------------------
#if data.skills.len() > 0 [
  #section("Skills")
  #for group in data.skills [
    #block(below: 0.35em)[
      #text(weight: "bold")[#group.category:] #group.items.join(", ")
    ]
  ]
]

// --- Experience ----------------------------------------------------------
#if data.experience.len() > 0 [
  #section("Experience")
  #for role in data.experience [
    // Dates stay INLINE. Pushing them right with #h(1fr) looks better but
    // reorders PDF text extraction: the date lands mid-sentence inside the
    // first bullet, so an ATS reads mangled prose. verify_pdf catches it.
    #block(above: 0.8em, below: 0.4em, breakable: false)[
      #text(weight: "bold")[#role.title], #role.company --- #role.location  |  #role.dates
    ]
    #list(..role.bullets)
  ]
]

// --- Education -----------------------------------------------------------
#if data.education.len() > 0 [
  #section("Education")
  #for item in data.education [
    #block(above: 0.6em, below: 0.2em)[
      #text(weight: "bold")[#item.credential], #item.institution --- #item.location  |  #item.dates
      #if item.at("note") != none [
        #linebreak()
        #text(size: 9.5pt)[#item.note]
      ]
    ]
  ]
]
