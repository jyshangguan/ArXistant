#!/usr/bin/env python3
"""
Weighted keyword extraction and interest regeneration for arXiv paper ranking.

This module provides:
- Noun phrase extraction from astro-ph text
- Domain filtering (stopwords, institutions, generic scientific terms)
- Weighted scoring: source_weight × frequency × specificity
- Top-50 pruning with 1-10 weight scale
- Weighted paper scoring using the interests file

Usage:
    import interest_generator
    interest_generator.regenerate_interests(db_path, interests_file)
"""

import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

# =============================================================================
# DOMAIN STOPWORDS — Generic scientific writing words that should NOT be keywords
# =============================================================================
GENERIC_STOPWORDS = {
    # Common verbs / writing words
    'able', 'about', 'above', 'absence', 'absolute', 'abstract', 'accepted', 'according', 'accordingly',
    'accuracy', 'accurate', 'achieved', 'across', 'act', 'action', 'actions', 'active', 'actively',
    'actual', 'actually', 'add', 'added', 'adding', 'addition', 'additional', 'address', 'adopt',
    'adopted', 'advantage', 'affect', 'affected', 'after', 'again', 'against', 'agree', 'agreement',
    'aim', 'aimed', 'all', 'allow', 'allowed', 'allowing', 'allows', 'almost', 'alone', 'along',
    'already', 'also', 'although', 'always', 'among', 'amount', 'amounts', 'analyse', 'analysed',
    'analyses', 'analysing', 'analysis', 'analyzed', 'analyzing', 'and', 'another', 'any', 'anyone',
    'anything', 'apart', 'apparent', 'apparently', 'appear', 'appeared', 'appearing', 'appears',
    'application', 'applications', 'applied', 'apply', 'applying', 'approach', 'approaches',
    'appropriate', 'approximately', 'are', 'area', 'areas', 'around', 'arrive', 'arrived', 'arrives',
    'arriving', 'article', 'articles', 'as', 'ask', 'asked', 'asking', 'asks', 'assess', 'assessed',
    'assessing', 'assessment', 'assessments', 'associated', 'assume', 'assumed', 'assumes',
    'assuming', 'assumption', 'assumptions', 'assure', 'assured', 'at', 'attempt', 'attempted',
    'attempting', 'attempts', 'attention', 'attract', 'attracted', 'attracting', 'attractive',
    'attracts', 'attribute', 'attributed', 'attributes', 'attributing', 'available', 'average',
    'avoid', 'avoided', 'avoiding', 'avoids', 'away', 'b', 'back', 'bad', 'based', 'basic',
    'basically', 'basis', 'be', 'became', 'because', 'become', 'becomes', 'becoming', 'been',
    'before', 'began', 'begin', 'beginning', 'begins', 'begun', 'behind', 'being', 'believe',
    'believed', 'believes', 'believing', 'below', 'benefit', 'benefited', 'benefiting', 'benefits',
    'best', 'better', 'between', 'beyond', 'big', 'bigger', 'biggest', 'bit', 'both', 'brief',
    'briefly', 'broad', 'broader', 'broadly', 'build', 'building', 'built', 'but', 'by', 'c',
    'call', 'called', 'calling', 'calls', 'can', 'cannot', 'capable', 'capability', 'carried',
    'carries', 'carry', 'carrying', 'case', 'cases', 'cause', 'caused', 'causes', 'causing',
    'certain', 'certainly', 'change', 'changed', 'changes', 'changing', 'check', 'checked',
    'checking', 'checks', 'choose', 'chose', 'chosen', 'choose', 'choosing', 'circumstance',
    'circumstances', 'claim', 'claimed', 'claiming', 'claims', 'clarify', 'clarified', 'clarifies',
    'clarifying', 'clear', 'clearer', 'clearly', 'close', 'closed', 'closely', 'closer', 'closes',
    'closing', 'combination', 'combine', 'combined', 'combines', 'combining', 'come', 'comes',
    'coming', 'comment', 'comments', 'common', 'commonly', 'comparable', 'compare', 'compared',
    'comparing', 'comparison', 'comparisons', 'compete', 'competed', 'competes', 'competing',
    'complete', 'completed', 'completely', 'completes', 'completing', 'complex', 'complicate',
    'complicated', 'complicates', 'complicating', 'complication', 'component', 'components',
    'comprise', 'comprised', 'comprises', 'comprising', 'concept', 'concepts', 'concerning',
    'conclude', 'concluded', 'concludes', 'concluding', 'conclusion', 'conclusions', 'condition',
    'conditions', 'conduct', 'conducted', 'conducting', 'conducts', 'confirm', 'confirmed',
    'confirming', 'confirms', 'connect', 'connected', 'connecting', 'connection', 'connections',
    'connects', 'consequence', 'consequences', 'consequently', 'consider', 'considerable',
    'considerably', 'consideration', 'considerations', 'considered', 'considering', 'considers',
    'consist', 'consisted', 'consisting', 'consists', 'consistent', 'consistently', 'constant',
    'constantly', 'constitute', 'constituted', 'constitutes', 'constituting', 'construct',
    'constructed', 'constructing', 'construction', 'constructs', 'contain', 'contained',
    'containing', 'contains', 'content', 'contents', 'context', 'contexts', 'continually',
    'continue', 'continued', 'continues', 'continuing', 'continuous', 'continuously', 'contribute',
    'contributed', 'contributes', 'contributing', 'contribution', 'contributions', 'control',
    'controlled', 'controlling', 'controls', 'conventional', 'conventionally', 'convert',
    'converted', 'converting', 'converts', 'correct', 'corrected', 'correcting', 'correction',
    'corrections', 'correctly', 'corrects', 'correspond', 'corresponded', 'corresponding',
    'corresponds', 'could', 'count', 'counted', 'counting', 'counts', 'course', 'courses',
    'cover', 'covered', 'covering', 'covers', 'create', 'created', 'creates', 'creating',
    'creation', 'criterion', 'criteria', 'current', 'currently', 'd', 'data', 'date', 'dates',
    'day', 'days', 'deal', 'dealing', 'deals', 'dealt', 'decide', 'decided', 'decides', 'deciding',
    'decision', 'decisions', 'declare', 'declared', 'declares', 'declaring', 'decrease',
    'decreased', 'decreases', 'decreasing', 'deduce', 'deduced', 'deduces', 'deducing', 'define',
    'defined', 'defines', 'defining', 'definite', 'definitely', 'definition', 'definitions',
    'degree', 'degrees', 'demonstrate', 'demonstrated', 'demonstrates', 'demonstrating',
    'demonstration', 'depend', 'depended', 'depending', 'depends', 'describe', 'described',
    'describes', 'describing', 'description', 'descriptions', 'despite', 'detail', 'detailed',
    'details', 'detect', 'detected', 'detecting', 'detects', 'determine', 'determined',
    'determines', 'determining', 'develop', 'developed', 'developing', 'development',
    'developments', 'develops', 'deviate', 'deviated', 'deviates', 'deviating', 'deviation',
    'deviations', 'did', 'differ', 'difference', 'differences', 'different', 'differently',
    'differs', 'difficult', 'difficulty', 'direct', 'directed', 'directing', 'direction',
    'directions', 'directly', 'directs', 'discover', 'discovered', 'discovering', 'discovers',
    'discovery', 'discuss', 'discussed', 'discusses', 'discussing', 'discussion', 'discussions',
    'display', 'displayed', 'displaying', 'displays', 'distinct', 'distinction', 'distinctions',
    'distinctly', 'distinguish', 'distinguished', 'distinguishes', 'distinguishing', 'do', 'does',
    'doing', 'done', 'doubt', 'doubts', 'down', 'draw', 'drawing', 'drawn', 'draws', 'drove',
    'driven', 'driving', 'drop', 'dropped', 'dropping', 'drops', 'due', 'during', 'e', 'each',
    'earlier', 'early', 'easier', 'easily', 'easy', 'effect', 'effective', 'effectively',
    'effects', 'efficient', 'efficiently', 'effort', 'efforts', 'eight', 'either', 'elaborate',
    'elaborated', 'elaborates', 'elaborating', 'else', 'elsewhere', 'emerge', 'emerged',
    'emergence', 'emerges', 'emerging', 'emphasis', 'emphasize', 'emphasized', 'emphasizes',
    'emphasizing', 'employ', 'employed', 'employing', 'employs', 'enable', 'enabled', 'enables',
    'enabling', 'encompass', 'encompassed', 'encompasses', 'encompassing', 'encounter',
    'encountered', 'encountering', 'encounters', 'encourage', 'encouraged', 'encourages',
    'encouraging', 'end', 'ended', 'ending', 'ends', 'enough', 'ensure', 'ensured', 'ensures',
    'ensuring', 'enter', 'entered', 'entering', 'enters', 'entire', 'entirely', 'equal',
    'equally', 'equals', 'equate', 'equated', 'equates', 'equating', 'equation', 'equations',
    'equivalent', 'equivalently', 'error', 'errors', 'especially', 'essentially', 'establish',
    'established', 'establishes', 'establishing', 'estimate', 'estimated', 'estimates',
    'estimating', 'estimation', 'estimations', 'etc', 'even', 'eventually', 'ever', 'every',
    'everybody', 'everyone', 'everything', 'everywhere', 'evident', 'evidently', 'exact',
    'exactly', 'examine', 'examined', 'examines', 'examining', 'example', 'examples', 'except',
    'exception', 'exceptions', 'excerpt', 'excerpts', 'excessive', 'excessively', 'exchange',
    'exchanged', 'exchanges', 'exchanging', 'exclude', 'excluded', 'excludes', 'excluding',
    'exclusion', 'exclusively', 'exhibit', 'exhibited', 'exhibiting', 'exhibits', 'exist',
    'existed', 'existing', 'exists', 'expand', 'expanded', 'expanding', 'expands', 'expansion',
    'expect', 'expected', 'expecting', 'expects', 'experience', 'experienced', 'experiences',
    'experiencing', 'experiment', 'experimental', 'experimentally', 'experiments', 'explain',
    'explained', 'explaining', 'explains', 'explanation', 'explanations', 'explicit', 'explicitly',
    'explore', 'explored', 'explores', 'exploring', 'exploration', 'export', 'exported',
    'exporting', 'exports', 'express', 'expressed', 'expresses', 'expressing', 'expression',
    'expressions', 'extend', 'extended', 'extending', 'extends', 'extension', 'extensive',
    'extensively', 'extent', 'extra', 'extract', 'extracted', 'extracting', 'extracts',
    'extremely', 'f', 'face', 'faced', 'faces', 'facing', 'fact', 'facts', 'factor', 'factors',
    'fair', 'fairly', 'fall', 'fallen', 'falling', 'falls', 'familiar', 'far', 'fashion',
    'fashions', 'fast', 'faster', 'fastest', 'favor', 'favorable', 'favorably', 'favored',
    'favoring', 'favors', 'favour', 'favourable', 'favourably', 'favoured', 'favouring',
    'favours', 'feature', 'featured', 'features', 'featuring', 'few', 'fewer', 'fewest',
    'fifth', 'figure', 'figured', 'figures', 'figuring', 'fill', 'filled', 'filling', 'fills',
    'final', 'finally', 'find', 'finding', 'findings', 'finds', 'fine', 'finely', 'finer',
    'finest', 'finish', 'finished', 'finishes', 'finishing', 'first', 'fit', 'fits', 'fitted',
    'fitting', 'five', 'fix', 'fixed', 'fixes', 'fixing', 'flow', 'flowed', 'flowing', 'flows',
    'focus', 'focused', 'focuses', 'focusing', 'focussed', 'focusses', 'focussing', 'follow',
    'followed', 'following', 'follows', 'for', 'force', 'forced', 'forces', 'forcing', 'form',
    'formal', 'formally', 'formed', 'forming', 'forms', 'formerly', 'forth', 'forty', 'found',
    'four', 'fourth', 'free', 'freely', 'from', 'front', 'full', 'fully', 'function',
    'functional', 'functionally', 'functioning', 'functions', 'further', 'furthermore', 'g',
    'gain', 'gained', 'gaining', 'gains', 'gave', 'general', 'generally', 'generate',
    'generated', 'generates', 'generating', 'generation', 'get', 'gets', 'getting', 'give',
    'given', 'gives', 'giving', 'go', 'goes', 'going', 'gone', 'good', 'got', 'gotten',
    'gradual', 'gradually', 'grant', 'granted', 'granting', 'grants', 'great', 'greater',
    'greatest', 'greatly', 'group', 'grouped', 'grouping', 'groups', 'grow', 'growing', 'grown',
    'grows', 'growth', 'guarantee', 'guaranteed', 'guaranteeing', 'guarantees', 'h', 'had',
    'half', 'happen', 'happened', 'happening', 'happens', 'hard', 'harder', 'hardest', 'hardly',
    'has', 'have', 'having', 'he', 'help', 'helped', 'helpful', 'helping', 'helps', 'hence',
    'her', 'here', 'hereafter', 'hereby', 'herein', 'hereupon', 'hers', 'herself', 'high',
    'higher', 'highest', 'highly', 'him', 'himself', 'his', 'hit', 'hits', 'hitting', 'hold',
    'holding', 'holds', 'how', 'however', 'hundred', 'hundreds', 'i', 'idea', 'ideas', 'identical',
    'identify', 'identified', 'identifies', 'identifying', 'if', 'illustrate', 'illustrated',
    'illustrates', 'illustrating', 'illustration', 'illustrations', 'imagine', 'imagined',
    'imagines', 'imagining', 'immediately', 'importance', 'important', 'importantly', 'impose',
    'imposed', 'imposes', 'imposing', 'improve', 'improved', 'improvement', 'improvements',
    'improves', 'improving', 'in', 'inability', 'inaccessible', 'inaccurate', 'inactive',
    'inappropriate', 'inability', 'incapable', 'include', 'included', 'includes', 'including',
    'inclusion', 'incomplete', 'incompletely', 'increase', 'increased', 'increases', 'increasing',
    'increasingly', 'indeed', 'independently', 'indicate', 'indicated', 'indicates', 'indicating',
    'indication', 'indications', 'indirect', 'indirectly', 'individual', 'individually',
    'infer', 'inferred', 'inferring', 'infers', 'influence', 'influenced', 'influences',
    'influencing', 'inform', 'information', 'informed', 'informing', 'informs', 'initial',
    'initially', 'innovation', 'innovations', 'input', 'inputs', 'inside', 'insight', 'insights',
    'insignificant', 'instead', 'insufficient', 'insufficiently', 'intend', 'intended',
    'intending', 'intends', 'intense', 'intensely', 'intensity', 'intent', 'intention',
    'intentions', 'interest', 'interested', 'interesting', 'interests', 'interfere', 'interfered',
    'interferes', 'interfering', 'interim', 'interior', 'internal', 'internally', 'interpret',
    'interpretation', 'interpretations', 'interpreted', 'interpreting', 'interprets', 'into',
    'introduce', 'introduced', 'introduces', 'introducing', 'introduction', 'introductions',
    'invaluable', 'invariably', 'investigate', 'investigated', 'investigates', 'investigating',
    'investigation', 'investigations', 'involve', 'involved', 'involves', 'involving', 'is', 'it',
    'its', 'itself', 'j', 'just', 'k', 'keep', 'keeping', 'keeps', 'kept', 'key', 'keys', 'kind',
    'kinds', 'know', 'knowing', 'known', 'knows', 'l', 'lack', 'lacked', 'lacking', 'lacks',
    'largely', 'last', 'lastly', 'late', 'later', 'latest', 'latter', 'latterly', 'lay', 'lays',
    'lead', 'leading', 'leads', 'led', 'left', 'length', 'less', 'lesser', 'let', 'lets', 'letter',
    'letters', 'level', 'levels', 'lie', 'lies', 'like', 'liked', 'likely', 'likewise', 'limit',
    'limited', 'limiting', 'limits', 'line', 'linear', 'lines', 'link', 'linked', 'linking',
    'links', 'list', 'listed', 'listing', 'lists', 'little', 'live', 'lived', 'lives', 'living',
    'load', 'loaded', 'loading', 'loads', 'local', 'locally', 'locate', 'located', 'locates',
    'locating', 'location', 'locations', 'long', 'longer', 'longest', 'look', 'looked', 'looking',
    'looks', 'lose', 'loses', 'losing', 'lost', 'lot', 'lots', 'low', 'lower', 'lowered',
    'lowering', 'lowers', 'lowest', 'm', 'made', 'main', 'mainly', 'maintain', 'maintained',
    'maintaining', 'maintains', 'major', 'majority', 'make', 'makes', 'making', 'manner',
    'many', 'mark', 'marked', 'marking', 'marks', 'match', 'matched', 'matches', 'matching',
    'matter', 'matters', 'may', 'maybe', 'mean', 'meaning', 'meaningful', 'means', 'meant',
    'meantime', 'meanwhile', 'measure', 'measured', 'measurement', 'measurements', 'measures',
    'measuring', 'mechanism', 'mechanisms', 'medium', 'meet', 'meeting', 'meets', 'met', 'method',
    'methodology', 'methods', 'might', 'million', 'millions', 'minor', 'minute', 'minutes', 'miss',
    'missed', 'misses', 'missing', 'mistake', 'mistaken', 'mistakes', 'mix', 'mixed', 'mixes',
    'mixing', 'model', 'modeled', 'modeling', 'modelled', 'modelling', 'models', 'modern',
    'modest', 'modestly', 'modification', 'modifications', 'modified', 'modifies', 'modify',
    'modifying', 'moment', 'moments', 'more', 'moreover', 'morning', 'most', 'mostly', 'move',
    'moved', 'movement', 'movements', 'moves', 'moving', 'much', 'multiple', 'must', 'my',
    'myself', 'n', 'name', 'named', 'namely', 'names', 'naming', 'narrow', 'narrower', 'nearly',
    'necessary', 'necessity', 'need', 'needed', 'needing', 'needs', 'negative', 'neither', 'never',
    'nevertheless', 'new', 'newly', 'next', 'nine', 'ninety', 'no', 'nobody', 'non', 'none',
    'nonetheless', 'noon', 'nor', 'normal', 'normally', 'not', 'note', 'noted', 'notes',
    'noting', 'nothing', 'notice', 'noticed', 'notices', 'noticing', 'notion', 'notions', 'now',
    'nowhere', 'number', 'numbers', 'numerous', 'o', 'obey', 'obeyed', 'obeying', 'obeys',
    'object', 'objective', 'objectively', 'objectives', 'objects', 'obtain', 'obtained',
    'obtaining', 'obtains', 'obvious', 'obviously', 'occasion', 'occasional', 'occasionally',
    'occasions', 'occur', 'occurred', 'occurring', 'occurs', 'of', 'off', 'offer', 'offered',
    'offering', 'offers', 'office', 'offices', 'often', 'oh', 'ok', 'okay', 'old', 'older',
    'oldest', 'on', 'once', 'one', 'ones', 'only', 'onto', 'open', 'opened', 'opening', 'opens',
    'operate', 'operated', 'operates', 'operating', 'operation', 'operations', 'opinion',
    'opinions', 'opportunity', 'opportunities', 'oppose', 'opposed', 'opposes', 'opposing',
    'opposite', 'or', 'oral', 'orally', 'order', 'ordered', 'ordering', 'orders', 'ordinary',
    'organism', 'organisms', 'organization', 'organizations', 'organize', 'organized', 'organizes',
    'organizing', 'origin', 'original', 'originally', 'origins', 'other', 'others', 'otherwise',
    'ought', 'our', 'ours', 'ourselves', 'out', 'outcome', 'outcomes', 'output', 'outputs',
    'outside', 'over', 'overall', 'overcome', 'overcomes', 'overcoming', 'overlook', 'overlooked',
    'overlooking', 'overlooks', 'owe', 'owed', 'owes', 'owing', 'own', 'p', 'page', 'pages',
    'paper', 'papers', 'par', 'part', 'particular', 'particularly', 'partly', 'parts', 'pass',
    'passed', 'passes', 'passing', 'past', 'path', 'paths', 'patient', 'patients', 'pattern',
    'patterns', 'pay', 'paying', 'pays', 'per', 'perfect', 'perfectly', 'perhaps', 'period',
    'periods', 'permanent', 'permanently', 'permit', 'permits', 'permitted', 'permitting', 'person',
    'personal', 'personally', 'persons', 'perspective', 'perspectives', 'pertain', 'pertained',
    'pertaining', 'pertains', 'phase', 'phases', 'phenomena', 'phenomenon', 'physical', 'physically',
    'place', 'placed', 'places', 'placing', 'plan', 'planned', 'planning', 'plans', 'play',
    'played', 'playing', 'plays', 'please', 'plenty', 'plus', 'point', 'pointed', 'pointing',
    'points', 'poor', 'poorly', 'popular', 'populate', 'populated', 'populates', 'populating',
    'population', 'populations', 'position', 'positioned', 'positioning', 'positions', 'positive',
    'positively', 'possess', 'possessed', 'possesses', 'possessing', 'possibility', 'possible',
    'possibly', 'post', 'posted', 'posting', 'posts', 'potential', 'potentially', 'power',
    'powerful', 'powers', 'practical', 'practically', 'practice', 'practices', 'pre', 'precede',
    'preceded', 'precedes', 'preceding', 'predict', 'predicted', 'predicting', 'predicts',
    'prediction', 'predictions', 'predominant', 'predominantly', 'prefer', 'preferable',
    'preferably', 'preferred', 'preferring', 'prefers', 'preliminary', 'premise', 'premises',
    'prepare', 'prepared', 'prepares', 'preparing', 'presence', 'present', 'presented',
    'presenting', 'presents', 'preserve', 'preserved', 'preserves', 'preserving', 'press',
    'pressed', 'presses', 'pressing', 'pressure', 'pressures', 'presumably', 'pretend', 'pretended',
    'pretending', 'pretends', 'pretty', 'prevent', 'prevented', 'preventing', 'prevents',
    'previous', 'previously', 'primarily', 'primary', 'prime', 'principle', 'principles', 'print',
    'printed', 'printing', 'prints', 'prior', 'prioritize', 'prioritized', 'prioritizes',
    'prioritizing', 'probably', 'problem', 'problems', 'procedure', 'procedures', 'proceed',
    'proceeded', 'proceeding', 'proceeds', 'process', 'processed', 'processes', 'processing',
    'produce', 'produced', 'produces', 'producing', 'product', 'production', 'products',
    'profession', 'professional', 'professionally', 'professionals', 'professions', 'profile',
    'profiles', 'program', 'programme', 'programmed', 'programmes', 'programming', 'programs',
    'progress', 'progressed', 'progresses', 'progressing', 'progressive', 'progressively',
    'project', 'projected', 'projecting', 'projects', 'prominent', 'prominently', 'promise',
    'promised', 'promises', 'promising', 'promote', 'promoted', 'promotes', 'promoting',
    'prompt', 'prompted', 'prompting', 'promptly', 'prompts', 'proper', 'properly', 'property',
    'properties', 'propose', 'proposed', 'proposes', 'proposing', 'proposition', 'propositions',
    'prospect', 'prospects', 'protect', 'protected', 'protecting', 'protects', 'prove', 'proved',
    'proven', 'proves', 'provide', 'provided', 'provides', 'providing', 'provision', 'provisions',
    'public', 'publication', 'publications', 'publicly', 'publish', 'published', 'publishes',
    'publishing', 'pull', 'pulled', 'pulling', 'pulls', 'purpose', 'purposes', 'push', 'pushed',
    'pushes', 'pushing', 'put', 'puts', 'putting', 'q', 'qualitative', 'qualitatively', 'quality',
    'quantitative', 'quantitatively', 'quantity', 'quarter', 'quarters', 'question', 'questioned',
    'questioning', 'questions', 'quick', 'quickly', 'quiet', 'quite', 'r', 'raise', 'raised',
    'raises', 'raising', 'ran', 'random', 'randomly', 'range', 'ranged', 'ranges', 'ranging', 'rare',
    'rarely', 'rate', 'rated', 'rates', 'rating', 'rather', 'reach', 'reached', 'reaches',
    'reaching', 'react', 'reacted', 'reacting', 'reaction', 'reactions', 'reacts', 'read',
    'readable', 'readily', 'reading', 'reads', 'ready', 'real', 'realistic', 'realistically',
    'reality', 'really', 'realize', 'realized', 'realizes', 'realizing', 'reason', 'reasonable',
    'reasonably', 'reasoned', 'reasoning', 'reasons', 'recall', 'recalled', 'recalling', 'recalls',
    'receive', 'received', 'receives', 'receiving', 'recent', 'recently', 'recognition',
    'recognize', 'recognized', 'recognizes', 'recognizing', 'recommend', 'recommendation',
    'recommendations', 'recommended', 'recommending', 'recommends', 'record', 'recorded',
    'recording', 'records', 'recover', 'recovered', 'recovering', 'recovers', 'recovery',
    'reduce', 'reduced', 'reduces', 'reducing', 'reduction', 'reductions', 'refer', 'reference',
    'referenced', 'references', 'referencing', 'referring', 'refers', 'reflect', 'reflected',
    'reflecting', 'reflection', 'reflects', 'refuse', 'refused', 'refuses', 'refusing', 'regard',
    'regarded', 'regarding', 'regards', 'regardless', 'region', 'regions', 'relate', 'related',
    'relates', 'relating', 'relation', 'relations', 'relationship', 'relationships', 'relative',
    'relatively', 'relevance', 'relevant', 'reliability', 'reliable', 'reliably', 'relied', 'relies',
    'relieve', 'relieved', 'relieves', 'relieving', 'rely', 'relying', 'remain', 'remainder',
    'remained', 'remaining', 'remains', 'remark', 'remarkable', 'remarkably', 'remarked',
    'remarking', 'remarks', 'remind', 'reminded', 'reminding', 'reminds', 'remote', 'remotely',
    'remove', 'removed', 'removes', 'removing', 'repeat', 'repeated', 'repeatedly', 'repeating',
    'repeats', 'replace', 'replaced', 'replacement', 'replaces', 'replacing', 'report',
    'reported', 'reporting', 'reports', 'represent', 'representation', 'representations',
    'represented', 'representing', 'represents', 'reproduce', 'reproduced', 'reproduces',
    'reproducing', 'reproduction', 'request', 'requested', 'requesting', 'requests', 'require',
    'required', 'requirement', 'requirements', 'requires', 'requiring', 'research', 'researched',
    'researches', 'researching', 'resemble', 'resembled', 'resembles', 'resembling', 'reserve',
    'reserved', 'reserves', 'reserving', 'reside', 'resided', 'resides', 'residing', 'residual',
    'residuals', 'resist', 'resisted', 'resisting', 'resists', 'resolution', 'resolutions',
    'resolve', 'resolved', 'resolves', 'resolving', 'resonance', 'resonances', 'resource',
    'resources', 'respect', 'respected', 'respecting', 'respects', 'respectively', 'respond',
    'responded', 'responding', 'response', 'responses', 'responds', 'responsibility',
    'responsible', 'rest', 'result', 'resulted', 'resulting', 'results', 'retain', 'retained',
    'retaining', 'retains', 'retire', 'retired', 'retires', 'retiring', 'retract', 'retracted',
    'retracting', 'retracts', 'retrieval', 'retrieve', 'retrieved', 'retrieves', 'retrieving',
    'return', 'returned', 'returning', 'returns', 'reveal', 'revealed', 'revealing', 'reveals',
    'review', 'reviewed', 'reviewing', 'reviews', 'revise', 'revised', 'revises', 'revising',
    'revision', 'revisions', 'revolution', 'revolutions', 'rich', 'ride', 'rides', 'riding',
    'right', 'rigid', 'rigidly', 'rise', 'rises', 'rising', 'risk', 'risks', 'road', 'roads',
    'role', 'roles', 'room', 'rooms', 'rough', 'roughly', 'round', 'row', 'rows', 'rule',
    'ruled', 'rules', 'ruling', 'run', 'running', 'runs', 's', 'sad', 'safe', 'safely', 'safer',
    'safest', 'safety', 'said', 'same', 'sample', 'sampled', 'samples', 'sampling', 'satisfactory',
    'satisfied', 'satisfies', 'satisfy', 'satisfying', 'save', 'saved', 'saves', 'saving', 'say',
    'saying', 'says', 'scale', 'scaled', 'scales', 'scaling', 'scenario', 'scenarios', 'scene',
    'scenes', 'scheme', 'schemes', 'school', 'schools', 'science', 'sciences', 'scientific',
    'scientifically', 'scope', 'score', 'scored', 'scores', 'scoring', 'screen', 'screened',
    'screening', 'screens', 'search', 'searched', 'searches', 'searching', 'second', 'secondary',
    'secondly', 'seconds', 'secret', 'secretly', 'secrets', 'section', 'sections', 'secure',
    'securely', 'securing', 'security', 'see', 'seeing', 'seek', 'seeking', 'seeks', 'seem',
    'seemed', 'seeming', 'seemingly', 'seems', 'seen', 'sees', 'select', 'selected', 'selecting',
    'selection', 'selections', 'selective', 'selectively', 'selects', 'self', 'sense', 'senses',
    'sensible', 'sensibly', 'sensitive', 'sensitively', 'sensitivity', 'sent', 'separate',
    'separated', 'separately', 'separates', 'separating', 'sequence', 'sequences', 'series',
    'serious', 'seriously', 'serve', 'served', 'serves', 'service', 'services', 'serving',
    'set', 'sets', 'setting', 'settings', 'settle', 'settled', 'settles', 'settling', 'seven',
    'several', 'severe', 'severely', 'sex', 'shall', 'shape', 'shaped', 'shapes', 'shaping',
    'share', 'shared', 'shares', 'sharing', 'sharp', 'sharply', 'she', 'shed', 'sheds', 'shift',
    'shifted', 'shifting', 'shifts', 'short', 'shorter', 'shortest', 'shortly', 'should',
    'show', 'showed', 'showing', 'shown', 'shows', 'shut', 'shuts', 'shutting', 'side', 'sides',
    'sign', 'signal', 'signals', 'signed', 'significant', 'significantly', 'signing', 'signs',
    'similar', 'similarity', 'similarly', 'simple', 'simpler', 'simplest', 'simply', 'since',
    'sing', 'singing', 'sings', 'single', 'singular', 'sit', 'site', 'sites', 'sits', 'sitting',
    'situation', 'situations', 'six', 'size', 'sizes', 'skill', 'skills', 'slight', 'slightly',
    'slow', 'slowly', 'small', 'smaller', 'smallest', 'smooth', 'smoothly', 'so', 'social',
    'socially', 'soft', 'softly', 'software', 'solar', 'sole', 'solely', 'solid', 'solution',
    'solutions', 'solve', 'solved', 'solves', 'solving', 'some', 'somebody', 'somehow', 'someone',
    'something', 'sometime', 'sometimes', 'somewhat', 'somewhere', 'soon', 'sooner', 'soonest',
    'sort', 'sorted', 'sorting', 'sorts', 'sound', 'sounded', 'sounding', 'sounds', 'source',
    'sources', 'south', 'southern', 'space', 'spaces', 'span', 'spans', 'speak', 'speaking',
    'speaks', 'special', 'specially', 'specific', 'specifically', 'specified', 'specifies',
    'specify', 'specifying', 'specimen', 'specimens', 'spectrum', 'speech', 'speed', 'spend',
    'spending', 'spends', 'spent', 'spite', 'split', 'splits', 'splitting', 'spoken', 'spoke',
    'spot', 'spots', 'spread', 'spreading', 'spreads', 'spring', 'square', 'squared', 'squares',
    'squaring', 'stable', 'stably', 'staff', 'stage', 'stages', 'stake', 'stakes', 'stand',
    'standard', 'standards', 'standing', 'stands', 'start', 'started', 'starting', 'starts',
    'state', 'stated', 'statement', 'statements', 'states', 'stating', 'station', 'stations',
    'status', 'stay', 'stayed', 'staying', 'stays', 'steady', 'steadily', 'step', 'steps',
    'still', 'stop', 'stopped', 'stopping', 'stops', 'storage', 'store', 'stored', 'stores',
    'storing', 'straight', 'strain', 'strains', 'strange', 'strategy', 'stream', 'streams',
    'street', 'streets', 'strength', 'strengthen', 'strengthened', 'strengthening', 'strengthens',
    'strengths', 'stress', 'stressed', 'stresses', 'stressing', 'stretch', 'stretched', 'stretches',
    'stretching', 'strict', 'strictly', 'strike', 'strikes', 'striking', 'string', 'strings',
    'strip', 'stripped', 'stripping', 'strips', 'strong', 'stronger', 'strongest', 'strongly',
    'struck', 'structure', 'structured', 'structures', 'structuring', 'struggle', 'struggled',
    'struggles', 'struggling', 'student', 'students', 'study', 'studied', 'studies', 'studying',
    'subject', 'subjects', 'subsequent', 'subsequently', 'substance', 'substances', 'substantial',
    'substantially', 'substitute', 'substituted', 'substitutes', 'substituting', 'succeed',
    'succeeded', 'succeeding', 'succeeds', 'success', 'successful', 'successfully', 'such',
    'sudden', 'suddenly', 'suffer', 'suffered', 'suffering', 'suffers', 'sufficient',
    'sufficiently', 'suggest', 'suggested', 'suggesting', 'suggestion', 'suggestions', 'suggests',
    'suitable', 'sum', 'summaries', 'summarize', 'summarized', 'summarizes', 'summarizing',
    'summary', 'summed', 'summing', 'sums', 'superior', 'supplement', 'supplemental',
    'supplementary', 'supplemented', 'supplementing', 'supplements', 'supply', 'supplied',
    'supplies', 'supplying', 'support', 'supported', 'supporting', 'supports', 'suppose',
    'supposed', 'supposes', 'supposing', 'sure', 'surely', 'surface', 'surfaces', 'surprise',
    'surprised', 'surprises', 'surprising', 'surprisingly', 'survey', 'surveyed', 'surveying',
    'surveys', 'survive', 'survived', 'survives', 'surviving', 'suspect', 'suspected',
    'suspecting', 'suspects', 'sustain', 'sustained', 'sustaining', 'sustains', 'switch',
    'switched', 'switches', 'switching', 'symbol', 'symbols', 'symptom', 'symptoms', 'system',
    'systematic', 'systematically', 'systems', 't', 'table', 'tables', 'take', 'taken', 'takes',
    'taking', 'talk', 'talked', 'talking', 'talks', 'tall', 'taller', 'tallest', 'tank', 'tanks',
    'task', 'tasks', 'taste', 'tasted', 'tastes', 'tasting', 'tax', 'taxes', 'teach', 'teacher',
    'teachers', 'teaches', 'teaching', 'team', 'teams', 'technical', 'technically', 'technique',
    'techniques', 'technology', 'technologies', 'tell', 'telling', 'tells', 'ten', 'tend', 'tended',
    'tending', 'tends', 'term', 'terms', 'test', 'tested', 'testing', 'tests', 'text', 'texts',
    'than', 'thank', 'thanks', 'that', 'the', 'their', 'them', 'themselves', 'then', 'theory',
    'theories', 'there', 'thereafter', 'thereby', 'therefore', 'therein', 'thereof', 'thereto',
    'these', 'they', 'thick', 'thicker', 'thickest', 'thin', 'thing', 'things', 'think', 'thinking',
    'thinks', 'third', 'thirty', 'this', 'those', 'though', 'thought', 'thousand', 'thousands',
    'three', 'through', 'throughout', 'throw', 'throwing', 'thrown', 'throws', 'thu', 'thus',
    'tight', 'tightly', 'time', 'times', 'tiny', 'tip', 'tired', 'to', 'today', 'together', 'too',
    'took', 'tool', 'tools', 'top', 'topic', 'topics', 'total', 'totally', 'touch', 'touched',
    'touches', 'touching', 'toward', 'towards', 'town', 'towns', 'track', 'tracked', 'tracking',
    'tracks', 'trade', 'traded', 'trades', 'trading', 'tradition', 'traditional', 'traditionally',
    'traffic', 'train', 'trained', 'training', 'trains', 'transfer', 'transferred', 'transferring',
    'transfers', 'transform', 'transformed', 'transforming', 'transforms', 'transition',
    'transitions', 'translate', 'translated', 'translates', 'translating', 'translation',
    'translations', 'transmit', 'transmitted', 'transmitting', 'transmits', 'transport',
    'transported', 'transporting', 'transports', 'travel', 'traveled', 'traveling', 'travels',
    'treat', 'treated', 'treating', 'treatment', 'treatments', 'treats', 'tree', 'trees', 'trend',
    'trends', 'trial', 'trials', 'trip', 'trips', 'trouble', 'true', 'truly', 'try', 'trying',
    'tune', 'tuned', 'tunes', 'tuning', 'turn', 'turned', 'turning', 'turns', 'twice', 'two',
    'type', 'typed', 'types', 'typing', 'typical', 'typically', 'u', 'unable', 'unacceptable',
    'uncertain', 'uncertainty', 'uncertainties', 'unclear', 'under', 'undergo', 'undergoes',
    'undergoing', 'undergone', 'understand', 'understanding', 'understands', 'understood',
    'undertake', 'undertaken', 'undertakes', 'undertaking', 'unfortunately', 'uniform',
    'uniformly', 'unimportant', 'union', 'unions', 'unique', 'uniquely', 'unit', 'units', 'unite',
    'united', 'unites', 'uniting', 'universal', 'universally', 'university', 'unless', 'unlike',
    'unlikely', 'until', 'unusual', 'unusually', 'up', 'upon', 'upper', 'upward', 'us', 'use',
    'used', 'useful', 'usefully', 'usefulness', 'useless', 'user', 'users', 'uses', 'using',
    'usual', 'usually', 'utility', 'v', 'valid', 'validity', 'valuable', 'value', 'valued',
    'values', 'valuing', 'variability', 'variable', 'variables', 'variably', 'variance',
    'variances', 'variant', 'variants', 'variation', 'variations', 'varied', 'varies', 'variety',
    'various', 'vary', 'varying', 'vast', 'vastly', 'very', 'via', 'view', 'viewed', 'viewing',
    'views', 'virtually', 'visible', 'visibly', 'visit', 'visited', 'visiting', 'visits', 'vital',
    'volume', 'volumes', 'w', 'want', 'wanted', 'wanting', 'wants', 'war', 'warm', 'warmer',
    'warmest', 'warn', 'warned', 'warning', 'warns', 'was', 'way', 'ways', 'we', 'weak', 'weaken',
    'weakened', 'weakening', 'weakens', 'weaker', 'weakest', 'weakly', 'wealth', 'wear', 'wearing',
    'wears', 'weather', 'week', 'weeks', 'weight', 'weighted', 'weights', 'weighing', 'welcome',
    'welcomed', 'welcomes', 'welcoming', 'well', 'went', 'were', 'what', 'whatever', 'when',
    'whenever', 'where', 'whereas', 'whereby', 'wherein', 'wherever', 'whether', 'which',
    'while', 'whilst', 'white', 'whites', 'who', 'whole', 'wholly', 'whom', 'whose', 'why', 'wide',
    'widely', 'wider', 'widest', 'width', 'will', 'willing', 'win', 'wins', 'winning', 'wish',
    'wished', 'wishes', 'wishing', 'with', 'within', 'without', 'won', 'wonder', 'wondered',
    'wonderful', 'wondering', 'wonders', 'word', 'words', 'work', 'worked', 'working', 'works',
    'world', 'worlds', 'worry', 'worried', 'worries', 'worrying', 'worse', 'worst', 'worth',
    'worthwhile', 'worthy', 'would', 'write', 'written', 'writer', 'writers', 'writes', 'writing',
    'wrong', 'wrote', 'x', 'y', 'year', 'yearly', 'years', 'yes', 'yesterday', 'yet', 'yield',
    'yielded', 'yielding', 'yields', 'you', 'young', 'younger', 'youngest', 'your', 'yourself',
    'yourselves', 'z', 'zero',
}

# Institution / affiliation words that should NOT be keywords
INSTITUTION_BLACKLIST = {
    'alpes', 'centra', 'cologne', 'cnrs', 'european', 'grenoble', 'gravita', 'institute',
    'institutes', 'institution', 'institutions', 'ipag', 'lesia', 'observatoire', 'observatory',
    'paris', 'psl', 'sorbonne', 'universit', 'universite', 'universities', 'university',
    'college', 'academy', 'center', 'centre', 'national', 'laboratory', 'lab', 'labs',
    'department', 'dept', 'division', 'max', 'planck', 'society', 'funding', 'grant', 'funded',
    'support', 'acknowledgment', 'acknowledgements', 'acknowledge', 'acknowledged', 'fellowship',
    'fellowships', 'scholarship', 'scholarships', 'stipend', 'stipends', 'award', 'awards',
    'prize', 'prizes', 'medal', 'medals', 'honor', 'honors', 'honour', 'honours', 'recipient',
    'recipients', 'fellow', 'fellows', 'member', 'members', 'membership', 'memberships',
    'society', 'societies', 'union', 'unions', 'association', 'associations', 'organization',
    'organizations', 'organisation', 'organisations', 'committee', 'committees', 'board',
    'boards', 'council', 'councils', 'panel', 'panels', 'commission', 'commissions', 'agency',
    'agencies', 'administration', 'administrations', 'government', 'governments', 'ministry',
    'ministries', 'bureau', 'bureaus', 'office', 'offices', 'foundation', 'foundations',
    'trust', 'trusts', 'charity', 'charities', 'ngo', 'ngos', 'corporation', 'corporations',
    'company', 'companies', 'firm', 'firms', 'enterprise', 'enterprises', 'industry', 'industries',
    'industrial', 'business', 'businesses', 'commercial', 'commercially', 'private', 'public',
    'nonprofit', 'nonprofits', 'non-profit', 'non-profits', 'profit', 'profits', 'profitable',
    'revenue', 'revenues', 'income', 'incomes', 'budget', 'budgets', 'expense', 'expenses',
    'cost', 'costs', 'price', 'prices', 'pricing', 'fee', 'fees', 'tuition', 'tuitions',
    'donation', 'donations', 'donor', 'donors', 'sponsor', 'sponsors', 'sponsorship',
    'sponsorships', 'patron', 'patrons', 'benefactor', 'benefactors', 'contributor', 'contributors',
    'subscription', 'subscriptions', 'membership', 'memberships', 'registration', 'registrations',
    'enrollment', 'enrollments', 'admission', 'admissions', 'application', 'applications',
    'applicant', 'applicants', 'candidate', 'candidates', 'nominee', 'nominees', 'appointee',
    'appointees', 'delegate', 'delegates', 'representative', 'representatives', 'ambassador',
    'ambassadors', 'envoy', 'envoys', 'diplomat', 'diplomats', 'consul', 'consuls', 'attaché',
    'attachés', ' attaché', 'attaché', 'commissioner', 'commissioners', 'governor', 'governors',
    'senator', 'senators', 'congressman', 'congressmen', 'congresswoman', 'congresswomen',
    'representative', 'representatives', 'mayor', 'mayors', 'councilman', 'councilmen',
    'councilwoman', 'councilwomen', 'alderman', 'aldermen', 'alderwoman', 'alderwomen',
    'supervisor', 'supervisors', 'trustee', 'trustees', 'regent', 'regents', 'chancellor',
    'chancellors', 'president', 'presidents', 'vice', 'provost', 'provosts', 'dean', 'deans',
    'chair', 'chairs', 'chairman', 'chairmen', 'chairwoman', 'chairwomen', 'director', 'directors',
    'head', 'heads', 'leader', 'leaders', 'manager', 'managers', 'superintendent', 'superintendents',
    'principal', 'principals', 'headmaster', 'headmasters', 'headmistress', 'headmistresses',
}

# Astrophysics-specific single words that ARE meaningful even as single words
ASTROPHYSICS_TERMS = {
    'agn', 'agns', 'alma', 'astrometry', 'astrophysics', 'atmosphere', 'atmospheric', 'aurora',
    'aurorae', 'auroras', 'baryon', 'baryons', 'big', 'bang', 'binary', 'binaries', 'bipolar',
    'blackbody', 'black', 'hole', 'holes', 'blazar', 'blazars', 'brown', 'dwarf', 'dwarfs',
    'bulge', 'bulges', 'carbon', 'cassiopeia', 'centaurus', 'cepheid', 'cepheids', 'chandrasekhar',
    'chandra', 'chemically', 'chemistry', 'circumstellar', 'cluster', 'clusters', 'cmb', 'comet',
    'comets', 'corona', 'coronal', 'cosmic', 'cosmology', 'crab', 'dark', 'matter', 'energy',
    'debris', 'degenerate', 'deuterium', 'disk', 'disks', 'disc', 'discs', 'doppler', 'dust',
    'dwarf', 'dwarfs', 'dynamical', 'dynamics', 'eclipse', 'eclipses', 'eclipsing', 'eddington',
    'einstein', 'electron', 'electrons', 'elliptical', 'emission', 'energy', 'envelope', 'envelopes',
    'epoch', 'equilibrium', 'equinox', 'erg', 'ergs', 'evolution', 'evolutionary', 'exoplanet',
    'exoplanets', 'extinction', 'feedback', 'fermi', 'field', 'fields', 'filament', 'filaments',
    'flare', 'flares', 'flux', 'fornax', 'frame', 'frequency', 'frequencies', 'fusion', 'galaxy',
    'galaxies', 'gamma', 'gas', 'giant', 'giants', 'globular', 'gravitation', 'gravitational',
    'gravity', 'grb', 'grbs', 'halo', 'halos', 'haloes', 'helium', 'herschel', 'hertzsprung',
    'higgs', 'hii', 'hubble', 'hydrogen', 'hypernova', 'hypernovae', 'infrared', 'instability',
    'instabilities', 'interferometer', 'interferometers', 'interferometric', 'interferometry',
    'intergalactic', 'interstellar', 'ionization', 'ionized', 'iron', 'ism', 'jet', 'jets',
    'jupiter', 'jupiters', 'jupiters', 'jupiters', 'kepler', 'kilonova', 'kilonovae', 'kinematics',
    'kpc', 'lensing', 'lightcurve', 'lightcurves', 'line', 'lines', 'lithium', 'lmc', 'luminosity',
    'luminous', 'm81', 'm82', 'm83', 'm87', 'magellanic', 'magnetic', 'magnetism', 'magnitude',
    'magnitudes', 'main', 'sequence', 'mariner', 'mas', 'maser', 'masers', 'mass', 'masses',
    'merger', 'mergers', 'metal', 'metallicity', 'metallicities', 'meteor', 'meteors', 'meteorite',
    'meteorites', 'microlensing', 'milky', 'millimeter', 'model', 'models', 'molecular', 'molecule',
    'molecules', 'nebula', 'nebulae', 'nebulas', 'neutrino', 'neutrinos', 'neutron', 'ngc', 'nir',
    'nova', 'novae', 'nuclear', 'nuclei', 'nucleus', 'nucleosynthesis', 'object', 'objects',
    'observable', 'observational', 'observatory', 'optical', 'orbit', 'orbital', 'orbits', 'outflow',
    'outflows', 'parallax', 'parse', 'parsec', 'parsecs', 'period', 'periodic', 'perturbation',
    'perturbations', 'photometry', 'photon', 'photons', 'planet', 'planets', 'planetary',
    'plasma', 'plasmas', 'polarimetry', 'polarization', 'population', 'pp', 'chain', 'precession',
    'progenitor', 'progenitors', 'prominence', 'prominences', 'proper', 'motion', 'proplyd',
    'proplyds', 'proton', 'protons', 'protoplanetary', 'pulsar', 'pulsars', 'qso', 'qsos',
    'quasar', 'quasars', 'radiation', 'radio', 'recombination', 'red', 'shift', 'shifts',
    'redshift', 'redshifts', 'reionization', 'relativity', 'resolution', 'resonance', 'resonances',
    'rotation', 'rr', 'lyrae', 'rv', 'sagittarius', 'satellite', 'satellites', 'scattering',
    'sed', 'self', 'seyfert', 'seyferts', 'shell', 'shells', 'shock', 'shocks', 'silicate',
    'silicon', 'simbad', 'sinfoni', 'smbh', 'smbhs', 'snc', 'solar', 'space', 'spacetime',
    'spectra', 'spectral', 'spectrograph', 'spectrographs', 'spectroscopy', 'spectrum', 'sphere',
    'spiral', 'spirals', 'spitzer', 'sfr', 'sfrs', 'star', 'stars', 'stellar', 'steward',
    'submillimeter', 'sun', 'sunspot', 'sunspots', 'supercluster', 'superclusters', 'supergiant',
    'supergiants', 'supernova', 'supernovae', 'supernovas', 'supershell', 'supershells', 'surface',
    'surveys', 'synchrotron', 'tauri', 'tde', 'tdes', 'telescope', 'telescopes', 'temperature',
    'temperatures', 'thermal', 'tidal', 'torus', 'tori', 'transit', 'transits', 'transiting',
    'transmission', 'turbulence', 'turbulent', 'ultraviolet', 'universe', 'uv', 'vapor', 'velocity',
    'velocities', 'virial', 'viscosity', 'visible', 'vla', 'vlba', 'vlt', 'vlti', 'void', 'voids',
    'wavelength', 'wavelengths', 'white', 'dwarf', 'winds', 'wmap', 'xrb', 'xrbs', 'xray', 'x-ray',
    'x-rays', 'xrays', 'yellow', 'yield', 'young', 'stellar', 'object', 'ysos', 'zodiacal',
    'zone', 'zones', 'zwicky',
}

# Additional known acronyms and instrument names (3+ uppercase letters)
KNOWN_ACRONYMS = {
    'alma', 'ao', 'aperture', 'apex', 'ccd', 'cfht', 'cobe', 'csst', 'deimos', 'desi', 'des',
    'dex', 'dss', 'eelt', 'elt', 'ero', 'eso', 'esoc', 'euclid', 'evla', 'faint', 'fgs', 'fos',
    'fwhm', 'gaia', 'gemini', 'galex', 'gbt', 'gmos', 'gpi', 'gravity', 'grb', 'gto', 'halpha',
    'hbeta', 'hst', 'halpha', 'hetdex', 'hii', 'hitomi', 'hrc', 'hw', 'icrs', 'ifu', 'imacs',
    'integral', 'ircs', 'iris', 'ism', 'iss', 'itra', 'jhk', 'jpass', 'jwst', 'keck', 'kepler',
    'kpc', 'kt', 'lbt', 'lco', 'lens', 'lbti', 'lris', 'lwa', 'm82', 'm87', 'maory', 'mcmc',
    'metis', 'micado', 'midi', 'mirk', 'miri', 'mle', 'mopra', 'muse', 'naco', 'nagoya', 'nice',
    'nicmos', 'nircam', 'niriss', 'nirspec', 'nustar', 'pacs', 'palomar', 'panstarrs', 'pdr',
    'pioneer', 'pionier', 'psf', 'roman', 'rxte', 'sami', 'sausage', 'sbs', 'scuba', 'sdss',
    'seit', 'self', 'shards', 'sinfoni', 'sloan', 'smos', 'snc', 'sofia', 'spherex', 'spitzer',
    'spire', 'ssh', 'stis', 'subaru', 'swift', 'tess', 'tmt', 'tng', 'tno', 'turbospec', 'uao',
    'uat', 'ukidss', 'unit', 'uv', 'uves', 'vhs', 'virgo', 'vis', 'vlba', 'vlt', 'vlti', 'vnir',
    'vos', 'vst', 'wfc', 'wfc3', 'wfirst', 'wise', 'wmap', 'xmm', 'xn', 'xs', 'xrism', 'xshooter',
    'xte', 'yso', 'ysos', 'zenith', '2mass', '2massxsc', '3d', 'allwise', 'cdfs', 'cosmos', 'goods',
}


# =============================================================================
# NOUN PHRASE EXTRACTION (regex-based, no NLP libraries needed)
# =============================================================================

def _clean_text(text):
    """Clean text for keyword extraction."""
    if not text:
        return ""
    text = text.lower()
    # Remove LaTeX
    text = re.sub(r'\\[a-zA-Z]+\*?(\{[^}]*\})*', ' ', text)
    # Remove math mode
    text = re.sub(r'\$[^$]*\$', ' ', text)
    # Remove URLs
    text = re.sub(r'http[s]?://\S+', ' ', text)
    # Remove citations [1], (Smith et al. 2020), etc.
    text = re.sub(r'\[\d+\]', ' ', text)
    text = re.sub(r'\([A-Z][a-z]+\s+et\s+al\.\s+\d{4}[a-z]?\)', ' ', text)
    # Replace underscores with spaces (they are not meaningful word separators in astrophysics text)
    text = text.replace('_', ' ')
    # Remove punctuation except hyphens and apostrophes within words
    text = re.sub(r'[^\w\s-]', ' ', text)
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text)
    # Strip leading/trailing hyphens from each word
    words = []
    for w in text.split():
        w = w.strip('-').strip("'")
        if w:
            words.append(w)
    return ' '.join(words)


def extract_noun_phrases(text, min_len=4, max_len=40):
    """
    Extract candidate noun phrases using regex patterns.
    Patterns: (adj|noun)+ (noun)+ — e.g., 'supermassive black hole', 'accretion disk'
    """
    text = _clean_text(text)
    if not text:
        return []
    
    phrases = []
    words = text.split()
    
    # Pattern 1: Adj/Noun + Noun (2-word)
    for i in range(len(words) - 1):
        w1, w2 = words[i], words[i + 1]
        if len(w1) >= 3 and len(w2) >= 3:
            if w1 not in GENERIC_STOPWORDS and w2 not in GENERIC_STOPWORDS:
                if w1 not in INSTITUTION_BLACKLIST and w2 not in INSTITUTION_BLACKLIST:
                    phrase = f"{w1} {w2}"
                    if min_len <= len(phrase) <= max_len:
                        phrases.append(phrase)
    
    # Pattern 2: Adj/Noun + Adj/Noun + Noun (3-word)
    for i in range(len(words) - 2):
        w1, w2, w3 = words[i], words[i + 1], words[i + 2]
        if all(len(w) >= 3 for w in (w1, w2, w3)):
            if all(w not in GENERIC_STOPWORDS for w in (w1, w2, w3)):
                if all(w not in INSTITUTION_BLACKLIST for w in (w1, w2, w3)):
                    phrase = f"{w1} {w2} {w3}"
                    if min_len <= len(phrase) <= max_len:
                        phrases.append(phrase)
    
    # Pattern 3: 4-word (rare but valuable)
    for i in range(len(words) - 3):
        w1, w2, w3, w4 = words[i], words[i + 1], words[i + 2], words[i + 3]
        if all(len(w) >= 3 for w in (w1, w2, w3, w4)):
            if all(w not in GENERIC_STOPWORDS for w in (w1, w2, w3, w4)):
                if all(w not in INSTITUTION_BLACKLIST for w in (w1, w2, w3, w4)):
                    phrase = f"{w1} {w2} {w3} {w4}"
                    if min_len <= len(phrase) <= max_len:
                        phrases.append(phrase)
    
    return phrases


def extract_acronyms(text):
    """Extract acronyms: 3+ uppercase letters, or known instrument names."""
    text = _clean_text(text)
    acronyms = []
    
    # Find ALL CAPS sequences of 3+ letters
    for match in re.finditer(r'\b[A-Z]{3,}\b', text.upper()):
        acr = match.group().lower()
        if acr not in GENERIC_STOPWORDS:
            acronyms.append(acr)
    
    # Also check for known acronyms in lowercase text
    words = text.split()
    for w in words:
        if w in KNOWN_ACRONYMS and w not in GENERIC_STOPWORDS:
            acronyms.append(w)
    
    return acronyms


def extract_single_astronomy_terms(text):
    """Extract single words that are known astrophysics terms."""
    text = _clean_text(text)
    terms = []
    for w in text.split():
        if len(w) >= 3 and w in ASTROPHYSICS_TERMS:
            terms.append(w)
    return terms


def extract_keywords_from_column(text):
    """Extract keywords from the curated 'keywords' column of publications."""
    if not text:
        return []
    
    keywords = []
    # Split by comma or semicolon
    for raw in re.split(r'[,;]', text):
        raw = raw.strip().lower()
        if not raw:
            continue
        # Remove category labels like "Astrophysics of Galaxies", "Physical Sciences"
        if raw in {'astrophysics of galaxies', 'astrophysics of stars', 'cosmology and nongalactic astrophysics',
                     'high energy astrophysical phenomena', 'earth and planetary astrophysics',
                     'instrumentation and methods for astrophysics', 'physical sciences',
                     'astronomical and space sciences', 'other physical sciences'}:
            continue
        # Remove "galaxies: " prefix style (keep the rest)
        raw = re.sub(r'^(galaxies|quasars|stars|planets|techniques):\s*', '', raw)
        raw = raw.strip()
        if raw and len(raw) >= 3 and raw not in GENERIC_STOPWORDS:
            keywords.append(raw)
    
    return keywords


# =============================================================================
# WEIGHTED SCORING
# =============================================================================

def regenerate_interests(db_path, interests_file):
    """
    Regenerate interests.txt from saved_papers and my_publications tables.
    Uses weighted scoring: source_weight × frequency × specificity.
    Keeps top 50 keywords with weights scaled to 1-10.
    """
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Collect all candidates with their source weights
    # Format: (phrase, source_weight)
    candidates = []
    
    # --- Source 1: my_publications keywords column (highest trust) ---
    c.execute("SELECT keywords FROM my_publications")
    for row in c.fetchall():
        keywords = row[0] or ''
        for kw in extract_keywords_from_column(keywords):
            candidates.append((kw, 5.0))
    
    # --- Source 2: my_publications titles (high trust) ---
    c.execute("SELECT title FROM my_publications")
    for row in c.fetchall():
        title = row[0] or ''
        for phrase in extract_noun_phrases(title):
            candidates.append((phrase, 3.0))
        for acr in extract_acronyms(title):
            candidates.append((acr, 2.0))
    
    # --- Source 3: my_publications abstracts (medium trust) ---
    c.execute("SELECT abstract FROM my_publications")
    for row in c.fetchall():
        abstract = row[0] or ''
        for phrase in extract_noun_phrases(abstract):
            candidates.append((phrase, 1.0))
        for acr in extract_acronyms(abstract):
            candidates.append((acr, 2.0))
    
    # --- Source 4: saved_papers titles (medium trust) ---
    c.execute("SELECT title FROM saved_papers")
    for row in c.fetchall():
        title = row[0] or ''
        for phrase in extract_noun_phrases(title):
            candidates.append((phrase, 2.0))
        for acr in extract_acronyms(title):
            candidates.append((acr, 2.0))
    
    # --- Source 5: saved_papers abstracts (lower trust) ---
    c.execute("SELECT abstract FROM saved_papers")
    for row in c.fetchall():
        abstract = row[0] or ''
        for phrase in extract_noun_phrases(abstract):
            candidates.append((phrase, 1.0))
        for acr in extract_acronyms(abstract):
            candidates.append((acr, 2.0))
    
    conn.close()
    
    # --- Pre-compute raw counts before filtering ---
    raw_counts = Counter()
    for phrase, _ in candidates:
        raw_counts[phrase.strip().lower()] += 1
    
    # --- Aggregate scores with filtering ---
    phrase_scores = defaultdict(float)
    phrase_counts = Counter()
    
    for phrase, source_weight in candidates:
        phrase = phrase.strip().lower()
        if len(phrase) < 3:
            continue
        # Single-word filter: must be astrophysics term or acronym
        word_count = phrase.count(' ') + 1
        if word_count == 1:
            if phrase not in ASTROPHYSICS_TERMS and phrase not in KNOWN_ACRONYMS:
                continue
        
        # Multi-word: filter if any word is too generic or contains digits
        words = phrase.split()
        if any(w in GENERIC_STOPWORDS for w in words):
            continue
        if any(w in INSTITUTION_BLACKLIST for w in words):
            continue
        # Filter out phrases with digits-only words or words starting/ending with hyphens
        if any(w.isdigit() or w.startswith('-') or w.endswith('-') for w in words):
            continue
        # Filter out words starting with digits (e.g., "2mass", "3d", "4k")
        if any(w[0].isdigit() for w in words if w):
            continue
        # Require at least 2 occurrences for multi-word phrases to avoid rare noise
        if word_count >= 2 and raw_counts[phrase] < 2:
            continue
        # Skip known incomplete telescope name fragments
        if 'atacama' in phrase and not phrase.endswith('array') and not phrase.endswith('alma'):
            continue
        # Skip phrases that look like fragments (contain standalone numbers or math)
        if any(len(w) == 1 and not w.isalpha() for w in words):
            continue

        
        phrase_scores[phrase] += source_weight
        phrase_counts[phrase] += 1
    
    # --- Apply specificity bonus and frequency multiplier ---
    final_scores = {}
    for phrase, base_score in phrase_scores.items():
        count = phrase_counts[phrase]
        word_count = phrase.count(' ') + 1
        
        # Specificity bonus
        if word_count >= 4:
            specificity = 2.5
        elif word_count == 3:
            specificity = 2.0
        elif word_count == 2:
            specificity = 1.5
        else:
            specificity = 1.0
        
        # Frequency multiplier (log scale to avoid runaway)
        freq_mult = 1.0 + 0.5 * (count - 1)
        
        final_scores[phrase] = base_score * specificity * freq_mult
    
    # --- Scale to 1-10 and keep top 50 ---
    if not final_scores:
        # Fallback: write empty file with header
        header = f"""# Research Interests — Auto-generated from saved papers + my publications
# Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
#
# Format: keyword<TAB>weight (1-10, 10 = highest relevance)
# This file is automatically regenerated whenever:
#   - A new paper is saved to the arXiv database
#   - The publications database is updated
# Lines starting with # are comments and will be ignored.

"""
        with open(interests_file, 'w', encoding='utf-8') as f:
            f.write(header)
        return 0
    
    max_score = max(final_scores.values())
    min_score = min(final_scores.values())
    score_range = max_score - min_score if max_score > min_score else 1.0
    
    # Scale to 1-10, round to nearest integer
    scaled = {}
    for phrase, score in final_scores.items():
        normalized = (score - min_score) / score_range
        scaled[phrase] = max(1, min(10, round(1 + normalized * 9)))
    
    # Sort by score descending, then alphabetically
    sorted_phrases = sorted(scaled.items(), key=lambda x: (-x[1], x[0]))
    top_50 = sorted_phrases[:50]
    
    # --- Write to file ---
    header = f"""# Research Interests — Auto-generated from saved papers + my publications
# Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
#
# Format: keyword<TAB>weight (1-10, 10 = highest relevance)
# This file is automatically regenerated whenever:
#   - A new paper is saved to the arXiv database
#   - The publications database is updated
# Lines starting with # are comments and will be ignored.

"""
    
    with open(interests_file, 'w', encoding='utf-8') as f:
        f.write(header)
        for phrase, weight in top_50:
            f.write(f"{phrase}\t{weight}\n")
    
    print(f"Regenerated interests.txt with {len(top_50)} keywords (scores 1-10)")
    return len(top_50)


# =============================================================================
# WEIGHTED PAPER SCORING
# =============================================================================

def load_interests_weighted(interests_file):
    """Load weighted interests from file. Returns list of (keyword, weight) tuples."""
    if not interests_file or not os.path.exists(interests_file):
        return []
    
    interests = []
    with open(interests_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                keyword = parts[0].strip()
                try:
                    weight = int(parts[1].strip())
                except ValueError:
                    weight = 1
            else:
                keyword = line
                weight = 1
            if keyword:
                interests.append((keyword, weight))
    
    return interests


def score_paper_weighted(paper, weighted_interests):
    """Score a paper using weighted keyword matching."""
    if not weighted_interests:
        return 0
    
    title_lower = paper.get('title', '').lower()
    text_lower = (paper.get('title', '') + ' ' + paper.get('abstract', '')).lower()
    score = 0
    
    for keyword, weight in weighted_interests:
        keyword_lower = keyword.lower()
        if keyword_lower in title_lower:
            score += 3 * weight
        elif keyword_lower in text_lower:
            score += 1 * weight
    
    return score


import os

if __name__ == '__main__':
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else 'local/arxiv_papers.db'
    interests = sys.argv[2] if len(sys.argv) > 2 else 'local/interests.txt'
    regenerate_interests(db, interests)
