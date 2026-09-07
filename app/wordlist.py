"""Common-English wordlist backing two safety gates.

1. Learning gate: an edit whose *target* is a common word is grammar/style,
   never vocabulary ("there" -> "their" must not become a memory entry).
2. Ambiguity gate: a matched misspelling that is a common word ("kiwi") needs
   contextual judgment before correction.

Curated rather than exhaustive, so it is never the only thing standing
between a term and an edit: the pipeline also withholds a deterministic
correction from any lowercase spelling it has not seen the user make
(pipeline._unproven_spelling), which covers ordinary words missing from this
list. Extending the list is a one-line change. Both points are stated in the
README's limitations.
"""

def is_commonish(word: str) -> bool:
    """Is this (or its obvious singular) a common English word? Shared by the
    ambiguity gate and the guard-conditionality decision."""
    w = word.casefold()
    bases = {w, w[:-1] if w.endswith("s") else w, w[:-2] if w.endswith("es") else w}
    return any(b in COMMON_WORDS for b in bases)


COMMON_WORDS: frozenset[str] = frozenset("""
a about above across act add after again against age ago agree air all
almost alone along already also although always am among amount an and angry
animal announce another answer any anyone anything appear apple april area
arm around arrive art as ask at ate august autumn away baby back bad bag
ball banana bank base be bear beat beautiful because become bed been before
began begin behind believe bell below beside best better between big bird
birthday bit black blue board boat body book both bottle bottom box boy
branch bread break breakfast bridge bright bring brother brought brown
budget build burn bus business busy but buy by call came can capital car
card care carry case cash cat catch cause cell cent center certain chair
chance change character charge chart check chicken child choose circle city
class clean clear climb clock close cloud coast coat coffee cold college
color come common company complete computer condition consider contain
continue control cook cool copy corner correct cost cotton could count
country course cover cow create cross crowd cry cup current cut dance dark
data date daughter day dead deal dear december decide deep demand demo
describe desert design desk detail develop dictionary did die difference
different difficult dinner direct discuss distance divide do doctor does dog
dollar done door double down draw dream dress drink drive drop dry during
each ear early earth east easy eat edge effect egg eight either electric
else end energy engine english enjoy enough enter entire equal escape
especially even evening ever every everyone everything exact example except
exercise expect experience explain express eye face fact fall family famous
far farm fast father fear february feed feel feet fell felt few field fifty
fight figure fill final finally find fine finger finish fire first fish fit
five fix floor flower fly follow food foot for force forest forget form
forward found four free fresh friday friend from front fruit full fun future
game garden gas gate gave general get girl give glass go goes gold gone good
got government grass great green ground group grow guess had hair half hand
happen happy hard has hat have he head hear heard heart heat heavy held help
her here high hill him his history hit hold hole home hope horse hot hour
house how huge human hundred hungry hurry hurt i ice idea if important in
inch include indeed india indian information inside instead interest into
invoice iron is island it its january job join joy july june just keep kept
key kind king kitchen knew know known kiwi lady lake land language large
last late later laugh law lay lead learn least leave led left leg less let
letter level lie life light like line link list listen little live local log
long look lost lot loud love low machine made mail main make man mango many
map march mark market matter may maybe me mean measure meat meet meeting
member men metal middle might mile milk million mind mine minute miss mister
modern moment monday money month moon more morning most mother mountain
mouth move much music must my name nation nature near nearly necessary neck
need never new next nice night nine no north nose not note nothing notice
november now number object observe ocean october of off office often oh oil
old on once one only open or orange order other our out outside over own
page pain paint pair paper part party pass past pattern pay peace people per
perhaps person phone pick picture piece place plan plane plant play please
plural point poor position possible pound power practice prepare present
president press pretty price print probably problem process produce product
project promise proud provide pull push put question quick quiet quite race
radio rain raise ran rather reach read ready real really reason receive
record red region remember repeat reply report represent require rest result
return review rich ride right ring rise river road rock roll room root rope
rose round row rule run safe said sail salt same sand sat saturday save saw
say scale school science score sea season seat second section see seem seen
sell send sense sent sentence september serve service set settle seven
several shape share sharp she ship shop short should shoulder shout show
side sign silver similar simple since sing single sister sit six size sleep
slow small smile snow so soft soil sold soldier some someone something
sometimes son song soon sound south space speak special speed spell spend
spoke sport spread spring square staff stand star start state station stay
steel step still stone stood stop store story straight strange stream street
strong student study subject success such suddenly sugar summer sun sunday
supply support sure surface sync system table tail take talk tall team tell
temperature ten term test than that the their them then there these they
thing think third thirty this those though thought thousand three through
thursday thus ticket time tiny to today together told tomorrow tone too took
tool top total touch toward town track trade train travel tree trip trouble
true try tuesday turn twenty two type under understand unit until up upon us
use usual valley value various very view visit voice wait walk wall want war
warm was watch water wave way we wear weather wednesday week weight well
went were west what wheel when where whether which while white who whole
whose why wide wife wild will win wind window winter wish with within
without woman women wonder wood word work world would write written wrong
yard year yellow yes yesterday yet you young your berry baker bush cherry
olive hazel daisy lily jasmine ruby pearl amber ivy holly heather robin
raven fox wolf hawk crane drake finch sparrow colt hunter mason carter
cooper smith taylor potter miller walker parker tanner piper sage basil
ginger pepper honey candy dawn eve sky brook meadow fern iris violet poppy
tulip angel faith grace charity harmony melody reed cliff glen dale rocky
sandy dusty rusty buck tiger leo wells banks bobby buddy bunny chase cloudy
dew dolly jolly liberty lucky major minor misty noble rainy ram rosy sonny
stormy sunny windy brooks rivers marsh lane fields gates bond frost storm
gale reeds clay
""".split())
