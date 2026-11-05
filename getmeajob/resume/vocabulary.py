"""Skill/tool vocabulary grouped by job field. Matching uses whole terms only, never guesses."""
from functools import lru_cache
import hashlib
import json
import re

# canonical term -> aliases that mean the same thing. Short, ambiguous words are avoided.
TERMS = {
    'technology': {
        'python': (), 'java': (), 'javascript': ('js', 'node.js', 'nodejs'), 'typescript': (), 'c++': (), 'c#': (),
        'golang': (), 'rust': (), 'ruby': ('ruby on rails', 'rails'), 'php': (), 'swift': (), 'kotlin': (),
        'scala': (), 'react': ('react.js', 'reactjs'), 'angular': (), 'vue': ('vue.js',), 'html': (), 'css': (),
        'sql': ('t-sql', 'postgresql', 'postgres', 'mysql'), 'sqlite': (), 'nosql': ('mongodb', 'dynamodb'),
        'fastapi': (), 'django': (), 'flask': (), 'spring': ('spring boot',), 'rest api': ('restful', 'rest apis'),
        'graphql': (), 'git': ('github', 'gitlab'), 'linux': (), 'aws': ('amazon web services',), 'azure': (),
        'gcp': ('google cloud',), 'docker': (), 'kubernetes': ('k8s',), 'terraform': (), 'ci/cd': ('continuous integration',),
        'devops': (), 'microservices': (), 'agile': ('scrum',), 'cybersecurity': ('information security', 'infosec'),
        'networking': (), 'testing': ('unit testing', 'test automation'), 'machine learning': ('ml',),
        'artificial intelligence': ('ai', 'llm', 'large language models'), 'deep learning': (), 'nlp': ('natural language processing',),
    },
    'data analytics': {
        'excel': ('ms excel', 'microsoft excel', 'spreadsheets'), 'power bi': ('powerbi',), 'tableau': (), 'looker': (),
        'pandas': (), 'numpy': (), 'r': (), 'spark': ('pyspark',), 'data analysis': ('data analytics', 'analytics'),
        'data visualization': ('dashboards', 'dashboarding'), 'statistics': ('statistical analysis',),
        'etl': ('data pipelines',), 'data modeling': ('data modelling',), 'a/b testing': ('experimentation',),
        'data warehouse': ('snowflake', 'bigquery', 'redshift'), 'dbt': (), 'reporting': (), 'forecasting': (),
        'business intelligence': ('bi',), 'data science': (), 'vba': (), 'google sheets': (),
    },
    'finance': {
        'financial modeling': ('financial modelling', 'financial models'), 'valuation': ('dcf',), 'budgeting': ('budget',),
        'forecasting': (), 'financial analysis': ('financial statements analysis',), 'investment': ('investments',),
        'portfolio': ('portfolio management',), 'risk management': ('risk analysis', 'credit risk', 'market risk'),
        'capital markets': ('equities', 'fixed income'), 'banking': ('investment banking',), 'treasury': (),
        'fp&a': ('financial planning', 'financial planning and analysis'), 'bloomberg': (), 'derivatives': (),
        'compliance': ('regulatory', 'kyc', 'aml'), 'fintech': (), 'payments': (), 'insurance': ('underwriting',),
    },
    'accounting': {
        'accounting': ('accountant',), 'bookkeeping': (), 'accounts payable': ('ap',), 'accounts receivable': ('ar',),
        'general ledger': ('gl',), 'reconciliation': ('reconciliations',), 'audit': ('auditing', 'internal audit'),
        'tax': ('taxation',), 'gaap': ('ifrs',), 'month-end close': ('month end close', 'financial close'),
        'quickbooks': (), 'sap': (), 'netsuite': (), 'cpa': ('chartered professional accountant',), 'payroll': (),
    },
    'consulting': {
        'consulting': ('consultant',), 'strategy': ('strategic planning',), 'stakeholder management': ('stakeholders',),
        'project management': ('pmp',), 'process improvement': ('lean', 'six sigma'), 'change management': (),
        'business analysis': ('business analyst', 'requirements gathering'), 'market research': ('competitive analysis',),
        'presentations': ('powerpoint',), 'client management': ('client facing', 'client-facing'),
    },
    'marketing': {
        'marketing': (), 'seo': ('search engine optimization',), 'sem': ('google ads', 'ppc'), 'social media': (),
        'content marketing': ('content creation', 'copywriting'), 'email marketing': ('mailchimp', 'hubspot'),
        'brand': ('branding',), 'google analytics': (), 'campaign': ('campaigns',), 'crm': ('salesforce',),
        'adobe creative suite': ('photoshop', 'illustrator', 'figma', 'canva'), 'public relations': ('pr',),
        'sales': ('business development',), 'customer success': ('customer service',),
    },
    'hr': {
        'recruiting': ('recruitment', 'talent acquisition', 'sourcing'), 'onboarding': (), 'employee relations': (),
        'hris': ('workday hcm', 'bamboohr'), 'compensation': ('benefits',), 'human resources': ('hr',),
        'training and development': ('learning and development',), 'performance management': (),
    },
    'operations': {
        'operations': (), 'supply chain': ('logistics', 'procurement'), 'inventory': ('inventory management',),
        'vendor management': ('vendors',), 'scheduling': (), 'quality assurance': ('qa', 'quality control'),
        'process documentation': ('sop', 'standard operating procedures'), 'erp': (), 'customer support': (),
        'communication': ('written communication', 'verbal communication'), 'teamwork': ('collaboration',),
        'problem solving': ('problem-solving',), 'leadership': (),
    },
    'engineering': {
        'cad': ('autocad', 'solidworks'), 'matlab': (), 'simulink': (), 'embedded': ('firmware', 'microcontrollers'),
        'circuit design': ('pcb', 'electronics'), 'mechanical design': ('mechanical engineering',),
        'manufacturing': (), 'robotics': (), 'systems engineering': (), 'plc': (), 'control systems': (),
        'electrical engineering': (), 'civil engineering': (), 'technical writing': (),
    },
}

ALIAS = {}
for _field, _terms in TERMS.items():
    for _canonical, _aliases in _terms.items():
        for _name in (_canonical, *_aliases):
            ALIAS.setdefault(_name.casefold(), _canonical)

_LOWER = sorted((n for n in ALIAS if n not in {'r', 'ap', 'ar', 'gl', 'ai', 'ml', 'bi', 'sap', 'pr', 'qa', 'hr', 'sem', 'seo'}), key=len, reverse=True)
_BOUNDS = r'(?<![\w+#/&.-])(%s)(?![\w+#&]|-\w)'
_PATTERN = re.compile(_BOUNDS % '|'.join(re.escape(name) for name in _LOWER))
# Short or common-English aliases only count when written in capitals (R, SAP, HR).
_CAPITALS = re.compile(_BOUNDS % '|'.join(re.escape(name.upper()) for name in sorted(set(ALIAS) - set(_LOWER))))
VOCABULARY_HASH = hashlib.sha256(json.dumps(TERMS, sort_keys=True).encode()).hexdigest()[:16]


@lru_cache(maxsize=2048)
def _find(text):
    found = {}
    for hit in _PATTERN.finditer(text.casefold()):
        found.setdefault(ALIAS[hit.group(1)], hit.start())
    for hit in _CAPITALS.finditer(text):
        found.setdefault(ALIAS[hit.group(1).casefold()], hit.start())
    return tuple(sorted(found, key=found.get))


def find_terms(text):
    """Return canonical vocabulary terms present as whole terms, in order of first appearance."""
    return list(_find(text))


def term_field(term):
    return next(field for field, terms in TERMS.items() if term in terms)
