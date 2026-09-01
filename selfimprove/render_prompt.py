#!/usr/bin/env python3
"""Render a prompt template: replace {{VAR}} with env var VAR. Usage: render_prompt.py FILE"""
import os, re, sys
tpl = open(sys.argv[1], encoding="utf-8").read()
sys.stdout.write(re.sub(r"\{\{(\w+)\}\}", lambda m: os.environ.get(m.group(1), ""), tpl))
