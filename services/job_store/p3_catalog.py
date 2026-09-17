"""Read-only schema snapshot shared by migration and custody admission.

No row data, credentials, object OIDs or database name enter the fingerprint.
"""

AUTHORITY_CATALOG_SQL = """
SELECT jsonb_build_object(
  'tables',(SELECT jsonb_agg(jsonb_build_object(
    'name',c.relname,'owner',pg_get_userbyid(c.relowner),'kind',c.relkind,'persistence',c.relpersistence,
    'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,'replica_identity',c.relreplident,
    'acl',(SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(a.grantor),
      CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,a.privilege_type,a.is_grantable)
      ORDER BY a.grantor::regrole::text,a.grantee::regrole::text,a.privilege_type)
      FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a),
    'columns',(SELECT jsonb_agg(jsonb_build_array(a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),
      a.attnotnull,a.attidentity,a.attgenerated,a.attisdropped,pg_get_expr(d.adbin,d.adrelid),
      col.collname,ns.nspname,(SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(x.grantor),
        CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,x.privilege_type,x.is_grantable)
        ORDER BY x.grantor::regrole::text,x.grantee::regrole::text,x.privilege_type) FROM aclexplode(a.attacl) x)) ORDER BY a.attnum)
      FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
      LEFT JOIN pg_collation col ON col.oid=a.attcollation LEFT JOIN pg_namespace ns ON ns.oid=col.collnamespace
      WHERE a.attrelid=c.oid AND a.attnum>0),
    'constraints',(SELECT jsonb_agg(jsonb_build_array(k.conname,k.contype,pg_get_constraintdef(k.oid),
      k.convalidated,k.condeferrable,k.condeferred,k.conislocal,k.coninhcount,k.connoinherit,k.conparentid=0,
      k.confrelid::regclass::text,k.conkey,k.confkey,k.confupdtype,k.confdeltype,k.confmatchtype,
      (SELECT jsonb_build_array(pg_get_indexdef(i.indexrelid),i.indisunique,i.indisprimary,i.indisvalid,
         i.indisready,i.indislive,i.indimmediate,pg_get_expr(i.indpred,i.indrelid),pg_get_expr(i.indexprs,i.indrelid))
       FROM pg_index i WHERE i.indexrelid=k.conindid),
      (SELECT jsonb_agg(jsonb_build_array(t.tgrelid::regclass::text,t.tgfoid::regprocedure::text,t.tgtype,
         t.tgenabled,t.tgisinternal,t.tgdeferrable,t.tginitdeferred,t.tgnargs,encode(t.tgargs,'hex'),
         pg_get_expr(t.tgqual,t.tgrelid)) ORDER BY t.tgrelid::regclass::text,t.tgfoid::regprocedure::text,t.tgtype)
       FROM pg_trigger t WHERE t.tgconstraint=k.oid))
      ORDER BY k.conname) FROM pg_constraint k WHERE k.conrelid=c.oid),
    'indexes',(SELECT jsonb_agg(jsonb_build_array(ic.relname,pg_get_indexdef(i.indexrelid),
      i.indisunique,i.indisprimary,i.indisvalid,i.indisready,i.indislive,i.indimmediate,
      i.indisreplident,pg_get_expr(i.indpred,i.indrelid),pg_get_expr(i.indexprs,i.indrelid),i.indnullsnotdistinct)
      ORDER BY ic.relname) FROM pg_index i JOIN pg_class ic ON ic.oid=i.indexrelid WHERE i.indrelid=c.oid),
    'triggers',(SELECT jsonb_agg(jsonb_build_array(t.tgname,t.tgenabled,t.tgtype,t.tgfoid::regprocedure::text,
      t.tgisinternal,t.tgdeferrable,t.tginitdeferred,t.tgnargs,encode(t.tgargs,'hex'),t.tgattr::text,
      pg_get_expr(t.tgqual,t.tgrelid),t.tgoldtable,t.tgnewtable) ORDER BY t.tgname)
      FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
    ) ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname=ANY(CAST(:tables AS text[]))),
  'roles',(SELECT jsonb_agg(jsonb_build_array(rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,
      rolcanlogin,rolreplication,rolbypassrls,rolconnlimit,rolconfig) ORDER BY rolname)
    FROM pg_roles WHERE rolname IN ('trading_owner','trading_p3_owner','trading_p3_authority','trading_job_worker')),
  'memberships',(SELECT jsonb_agg(jsonb_build_array(r.rolname,m.rolname,pg_get_userbyid(a.grantor),
      a.admin_option,a.inherit_option,a.set_option) ORDER BY r.rolname,m.rolname,pg_get_userbyid(a.grantor))
    FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid JOIN pg_roles m ON m.oid=a.member
    WHERE r.rolname IN ('trading_owner','trading_p3_owner','trading_p3_authority','trading_job_worker')
       OR m.rolname IN ('trading_owner','trading_p3_owner','trading_p3_authority','trading_job_worker')),
  'schemas',(SELECT jsonb_agg(jsonb_build_array(n.nspname,pg_get_userbyid(n.nspowner),
      (SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(a.grantor),
        CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,a.privilege_type,a.is_grantable)
        ORDER BY a.grantor::regrole::text,a.grantee::regrole::text,a.privilege_type)
       FROM aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a)) ORDER BY n.nspname)
    FROM pg_namespace n WHERE n.nspname IN ('public','job_plane')),
  'defaults',(SELECT jsonb_agg(jsonb_build_array(coalesce(n.nspname,'GLOBAL'),d.defaclobjtype,
      (SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(a.grantor),
        CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,a.privilege_type,a.is_grantable)
        ORDER BY a.grantor::regrole::text,a.grantee::regrole::text,a.privilege_type) FROM aclexplode(d.defaclacl) a))
      ORDER BY coalesce(n.nspname,'GLOBAL'),d.defaclobjtype)
    FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
    WHERE d.defaclrole=(SELECT oid FROM pg_roles WHERE rolname='trading_p3_owner'))
)
"""

# Pin the full SQL dependency surface, including called helpers and append-only
# controls. The 0026 snapshot already covers schemas, core roles and defaults.
CUSTODIAN_CATALOG_SQL = """WITH authority AS ("""+AUTHORITY_CATALOG_SQL.replace(
    'CAST(:tables AS text[])',
    "ARRAY['p3_campaign_authorizations','p3_operation_authorizations','p3_operation_job_bindings',"
    + "'p3_holdout_disclosures','p3_custodian_releases','p3_alpha_job_commits','jobs','job_attempts',"
    + "'job_events','domain_events','event_outbox']")+"""), snapshot AS (
  SELECT jsonb_build_object('authority',(SELECT * FROM authority),
    'functions',(SELECT jsonb_agg(jsonb_build_array(n.nspname,p.proname,
      pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid),
      pg_get_userbyid(p.proowner),p.proleakproof,p.proisstrict,p.proparallel,
      (SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(a.grantor),
        CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
        a.privilege_type,a.is_grantable) ORDER BY a.grantor::regrole::text,a.grantee::regrole::text,a.privilege_type)
        FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a))
      ORDER BY n.nspname,p.proname,pg_get_function_identity_arguments(p.oid))
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname IN ('public','job_plane') AND p.prokind='f'),
    'roles',(SELECT jsonb_agg(jsonb_build_array(rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,
      rolcanlogin,rolreplication,rolbypassrls,rolconnlimit,rolconfig,rolvaliduntil) ORDER BY rolname)
      FROM pg_roles WHERE starts_with(rolname,'trading_')),
    'memberships',(SELECT jsonb_agg(jsonb_build_array(r.rolname,m.rolname,pg_get_userbyid(a.grantor),
      a.admin_option,a.inherit_option,a.set_option) ORDER BY r.rolname,m.rolname,pg_get_userbyid(a.grantor))
      FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid JOIN pg_roles m ON m.oid=a.member
      WHERE starts_with(r.rolname,'trading_') OR starts_with(m.rolname,'trading_')),
    'settings',(SELECT jsonb_agg(jsonb_build_array(CASE WHEN s.setrole=0 THEN 'ALL' ELSE pg_get_userbyid(s.setrole) END,
      s.setconfig) ORDER BY s.setrole::regrole::text) FROM pg_db_role_setting s
      WHERE s.setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database())),
    'rules',(SELECT jsonb_agg(pg_get_ruledef(w.oid) ORDER BY n.nspname,c.relname,w.rulename)
      FROM pg_rewrite w JOIN pg_class c ON c.oid=w.ev_class JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname IN ('public','job_plane')),
    'policies',(SELECT jsonb_agg(to_jsonb(p) ORDER BY schemaname,tablename,policyname)
      FROM pg_policies p WHERE schemaname IN ('public','job_plane'))
  ) AS value
) SELECT encode(sha256(convert_to(value::text,'UTF8')),'hex') AS catalog_sha256 FROM snapshot
"""

# Populated only from the reviewed disposable migration result, never at runtime.
CUSTODIAN_CATALOG_SHA256 = '9572607639ee26d2b294ce8468880fabe0fc9f6397eb739c1f6365b219b57c04'

# Preserve historical custody qualification; sessions also own terminal publication.
SESSION_CATALOG_SQL = CUSTODIAN_CATALOG_SQL.replace("'event_outbox']",
    "'event_outbox','p3_alpha_heads','p3_alpha_projection','event_append_idempotency',"
    "'event_publications','job_artifacts','worker_heartbeats']")

SESSION_REVISION = '0030_p3_session_terminal_fences'
SESSION_CATALOG_SHA256 = '506071c06cdd91fe7e9506a03c15df10949e41f97f80dbbdd27c305aef3a82d4'
