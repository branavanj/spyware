#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""
Essais de l'etape 0 : prouver le chemin technique du futur plugin Bareos
pour Nutanix AHV, sur une VM de TEST.

Aucune dependance : Python 3.9+ et sa bibliotheque standard.

Sous-commandes (dans l'ordre conseille) :
  check     lecture seule : versions d'API, VM, disques, cluster
  snapshot  cree puis supprime un point de recuperation (test licence/droits)
  cbt       deux points de recuperation, calcul des regions modifiees
  read      [sur la proxy, root] point -> Volume Group -> lecture, debit
  restore   [sur la proxy, root] copie complete vers une NOUVELLE VM eteinte
  cleanup   liste (et supprime avec --yes) tout objet nomme bareos-etape0-*

Tout objet cree porte le prefixe "bareos-etape0-". Les points de
recuperation expirent d'eux-memes au bout de 4 heures.
"""

import argparse
import base64
import getpass
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

PREFIX = "bareos-etape0-"
# Version la plus basse offrant la fonction voulue (voir cahier des charges 4.0).
V = {
    "dataprotection": "v4.0",
    "volumes": "v4.0",
    "vmm": "v4.0",
    "vmm_create": "v4.1",  # clonage d'un disque de Volume Group vers une VM
    "clustermgmt": "v4.0",
    "prism": "v4.0",
}
CHUNK = 4 * 1024 * 1024
GIB = 1024 ** 3
DEV = "/dev/"


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class ApiError(Exception):
    pass


class Prism:
    """Client REST minimal pour l'API v4 de Prism Central."""

    def __init__(self, host, user, password, ca=None, insecure=False):
        self.base = "https://%s:9440/api" % host
        token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
        self.auth = "Basic " + token
        if insecure:
            self.ctx = ssl._create_unverified_context()
        else:
            self.ctx = ssl.create_default_context(cafile=ca)

    def call(self, method, path, body=None, headers=None, base=None, auth=True):
        url = (base or self.base) + path
        hdrs = {"Accept": "application/json"}
        if auth:
            hdrs["Authorization"] = self.auth
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        if method != "GET":
            hdrs["NTNX-Request-Id"] = str(uuid.uuid4())
        hdrs.update(headers or {})
        for attempt in range(6):
            req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
            try:
                with urllib.request.urlopen(req, context=self.ctx, timeout=120) as r:
                    raw = r.read()
                    doc = json.loads(raw) if raw else {}
                    return doc, r.headers.get("ETag")
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                if e.code in (429, 503) and attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError("%s %s -> HTTP %s : %s" % (method, path, e.code, text[:1500]))
            except urllib.error.URLError as e:
                raise ApiError("%s %s -> %s" % (method, url, e.reason))
        raise ApiError("%s %s : trop de tentatives" % (method, path))

    def get(self, path):
        return self.call("GET", path)[0].get("data")

    def list(self, path, flt=None):
        """Liste complete, page par page."""
        out, page = [], 0
        while True:
            q = {"$page": page, "$limit": 100}
            if flt:
                q["$filter"] = flt
            doc = self.call("GET", path + "?" + urllib.parse.urlencode(q))[0]
            items = doc.get("data") or []
            if not isinstance(items, list):
                raise ApiError("reponse inattendue pour %s : %s" % (path, str(items)[:500]))
            out += items
            total = (doc.get("metadata") or {}).get("totalAvailableResults", len(out))
            if not items or len(out) >= total:
                return out
            page += 1

    def task(self, doc, what, timeout=1800):
        """Attend la fin de la tache renvoyee par un appel de modification."""
        ext = (doc.get("data") or {}).get("extId")
        if not ext:
            raise ApiError("%s : pas de tache dans la reponse : %s" % (what, str(doc)[:500]))
        path = "/prism/%s/config/tasks/%s" % (V["prism"], urllib.parse.quote(ext, safe=":="))
        end, delay = time.time() + timeout, 1
        while time.time() < end:
            t = self.get(path)
            st = t.get("status")
            if st == "SUCCEEDED":
                return t
            if st in ("FAILED", "CANCELED"):
                raise ApiError("%s : tache %s : %s %s" % (
                    what, st, json.dumps(t.get("errorMessages"))[:1500],
                    t.get("legacyErrorMessage") or ""))
            time.sleep(delay)
            delay = min(delay + 1, 10)
        raise ApiError("%s : delai depasse" % what)

    def do(self, method, path, body, what):
        log("  -> " + what)
        return self.task(self.call(method, path, body)[0], what)

    def delete(self, path, what):
        # If-Match n'est pas toujours exige pour DELETE, mais ne nuit pas.
        etag = self.call("GET", path)[1]
        hdrs = {"If-Match": etag} if etag else {}
        log("  -> suppression " + what)
        return self.task(self.call("DELETE", path, headers=hdrs)[0], "suppression " + what)


def affected(task, rel_part):
    for e in task.get("entitiesAffected") or []:
        if rel_part in (e.get("rel") or ""):
            return e.get("extId")
    return None


# ---------------------------------------------------------------- objets

def find_vm(p, name):
    vms = p.list("/vmm/%s/ahv/config/vms" % V["vmm"], "name eq '%s'" % name)
    vms = [v for v in vms if v.get("name") == name]
    if len(vms) != 1:
        raise ApiError("VM '%s' : %d trouvee(s), il en faut exactement une" % (name, len(vms)))
    return p.get("/vmm/%s/ahv/config/vms/%s" % (V["vmm"], vms[0]["extId"]))


def vm_disks(vm):
    out = []
    for d in vm.get("disks") or []:
        b = d.get("backingInfo") or {}
        if "VmDisk" not in b.get("$objectType", ""):
            continue  # Volume Group attache : hors perimetre
        out.append({
            "extId": d.get("extId"),
            "diskExtId": b.get("diskExtId"),
            "size": b.get("diskSizeBytes"),
            "container": (b.get("storageContainer") or {}).get("extId"),
            "address": d.get("diskAddress") or {},
        })
    return out


def create_rp(p, vm, label):
    name = "%s%s-%s-%d" % (PREFIX, vm["name"], label, int(time.time()))
    exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 4 * 3600))
    body = {"name": name, "expirationTime": exp, "recoveryPointType": "CRASH_CONSISTENT",
            "vmRecoveryPoints": [{"vmExtId": vm["extId"]}]}
    t = p.do("POST", "/dataprotection/%s/config/recovery-points" % V["dataprotection"],
             body, "creation du point de recuperation " + name)
    ext = affected(t, "recovery-point")
    if not ext:
        rps = [r for r in p.list("/dataprotection/%s/config/recovery-points"
                                 % V["dataprotection"]) if r.get("name") == name]
        if not rps:
            raise ApiError("point de recuperation cree mais introuvable : " + name)
        ext = rps[0]["extId"]
    rp = p.get("/dataprotection/%s/config/recovery-points/%s" % (V["dataprotection"], ext))
    vmrp = (rp.get("vmRecoveryPoints") or [{}])[0]
    info = {"extId": ext, "name": name, "vmrp": vmrp.get("extId"),
            "disks": vmrp.get("diskRecoveryPoints") or [], "isSecure": rp.get("isSecure")}
    log("     point %s, %d disque(s), isSecure=%s" % (ext, len(info["disks"]), info["isSecure"]))
    if not info["vmrp"] or not info["disks"]:
        raise ApiError("point de recuperation sans disque : %s" % json.dumps(rp)[:1500])
    return info


def delete_rp(p, rp):
    p.delete("/dataprotection/%s/config/recovery-points/%s" % (V["dataprotection"], rp["extId"]),
             "du point " + rp["name"])


def create_vg(p, cluster, label):
    name = "%s%s-%d" % (PREFIX, label, int(time.time()))
    body = {"name": name, "clusterReference": cluster, "usageType": "TEMPORARY",
            "description": "Essai etape 0 Bareos, supprimable sans risque"}
    t = p.do("POST", "/volumes/%s/config/volume-groups" % V["volumes"], body,
             "creation du Volume Group " + name)
    ext = affected(t, "volume-group")
    if not ext:
        vgs = [g for g in p.list("/volumes/%s/config/volume-groups" % V["volumes"])
               if g.get("name") == name]
        if not vgs:
            raise ApiError("Volume Group cree mais introuvable : " + name)
        ext = vgs[0]["extId"]
    return {"extId": ext, "name": name}


def add_vg_disk(p, vg, index, source_id, source_type, size=None):
    body = {"index": index,
            "diskDataSourceReference": {"extId": source_id, "entityType": source_type}}
    if size:
        body["diskSizeBytes"] = size
    p.do("POST", "/volumes/%s/config/volume-groups/%s/disks" % (V["volumes"], vg["extId"]),
         body, "ajout du disque %d (%s) a %s" % (index, source_type, vg["name"]))


def vg_disks(p, vg):
    ds = p.list("/volumes/%s/config/volume-groups/%s/disks" % (V["volumes"], vg["extId"]))
    return sorted(ds, key=lambda d: d.get("index", 0))


def attach(p, vg, proxy):
    p.do("POST", "/volumes/%s/config/volume-groups/%s/$actions/attach-vm"
         % (V["volumes"], vg["extId"]), {"extId": proxy["extId"]},
         "attachement de %s a la proxy" % vg["name"])


def detach(p, vg, proxy):
    p.do("POST", "/volumes/%s/config/volume-groups/%s/$actions/detach-vm"
         % (V["volumes"], vg["extId"]), {"extId": proxy["extId"]},
         "detachement de %s" % vg["name"])


def delete_vg(p, vg):
    p.delete("/volumes/%s/config/volume-groups/%s" % (V["volumes"], vg["extId"]),
             "du Volume Group " + vg["name"])


class Cleanup:
    """Pile d'actions de nettoyage, executee a l'envers quoi qu'il arrive."""

    def __init__(self):
        self.actions = []

    def add(self, what, fn):
        self.actions.append((what, fn))

    def drop(self, what):
        self.actions = [a for a in self.actions if a[0] != what]

    def run(self):
        ok = True
        for what, fn in reversed(self.actions):
            try:
                fn()
            except Exception as e:  # on continue le nettoyage coute que coute
                ok = False
                log("  !! nettoyage en echec (%s) : %s" % (what, e))
        self.actions = []
        if not ok:
            log("  !! relancer : ahv_etape0.py cleanup --yes")
        return ok


# ---------------------------------------------------------------- proxy

def block_devices():
    devs = {}
    for name in os.listdir("/sys/block"):
        if not name.startswith(("sd", "vd")):
            continue
        with open("/sys/block/%s/size" % name) as f:
            devs[name] = int(f.read()) * 512
    return devs


def wait_new_devices(before, count, timeout=90):
    end = time.time() + timeout
    new = {}
    while time.time() < end:
        now = block_devices()
        new = {n: s for n, s in now.items() if n not in before}
        if len(new) >= count:
            time.sleep(2)
            return new
        time.sleep(1)
    raise ApiError("attendu %d nouveau(x) disque(s) sur la proxy, vu %d : %s"
                   % (count, len(new), sorted(new)))


def describe_device(name):
    """Affiche ce qui permettra au plugin d'identifier le disque de facon sure."""
    links = []
    byid = "/dev/disk/by-id"
    if os.path.isdir(byid):
        for link in sorted(os.listdir(byid)):
            if os.path.realpath(os.path.join(byid, link)) == "/dev/" + name:
                links.append(link)
    log("     /dev/%s  liens by-id : %s" % (name, ", ".join(links) or "(aucun)"))
    try:
        out = subprocess.run(["lsblk", "-dno", "NAME,SIZE,SERIAL,WWN,VENDOR,MODEL", "/dev/" + name],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        log("     lsblk : " + out)
    except Exception as e:
        log("     lsblk indisponible : %s" % e)


def match_devices(disks, new):
    """Associe les disques du Volume Group (par ordre) aux peripheriques (par taille)."""
    res, free = [], dict(new)
    for d in disks:
        size = d.get("diskSizeBytes")
        cands = sorted(n for n, s in free.items() if s == size)
        if len(cands) != 1:
            raise ApiError("impossible d'associer le disque d'index %s (%s octets) : "
                           "candidats %s. Utiliser une VM de test a disque unique ou "
                           "a disques de tailles differentes." % (d.get("index"), size, cands))
        res.append(cands[0])
        del free[cands[0]]
    return res


def read_device(name, max_bytes):
    path = DEV + name
    total, zero, start = 0, 0, time.time()
    blank = bytes(CHUNK)
    with open(path, "rb", buffering=0) as f:
        while total < max_bytes:
            buf = f.read(min(CHUNK, max_bytes - total))
            if not buf:
                break
            total += len(buf)
            if buf == blank[:len(buf)]:
                zero += len(buf)
    dur = max(time.time() - start, 0.001)
    log("     %s : %.1f Gio lus en %.0f s, %.0f Mio/s, %.0f %% de zeros"
        % (path, total / GIB, dur, total / dur / 1048576, 100.0 * zero / max(total, 1)))


def copy_device(src, dst, size):
    blank = bytes(CHUNK)
    # Garde-fou : la cible vient d'etre creee vide. Si elle contient deja des
    # donnees, ce n'est pas le bon disque : on n'ecrit rien.
    with open(DEV + dst, "rb", buffering=0) as f:
        if f.read(CHUNK).strip(b"\0"):
            raise ApiError("/dev/%s n'est pas vide : ecriture refusee" % dst)
    done, written, start, last = 0, 0, time.time(), time.time()
    with open(DEV + src, "rb", buffering=0) as fi, open(DEV + dst, "r+b", buffering=0) as fo:
        while done < size:
            buf = fi.read(min(CHUNK, size - done))
            if not buf:
                raise ApiError("lecture tronquee de /dev/%s a %d octets" % (src, done))
            if buf != blank[:len(buf)]:
                fo.seek(done)
                fo.write(buf)
                written += len(buf)
            done += len(buf)
            if time.time() - last > 30:
                last = time.time()
                log("     copie %s -> %s : %.0f %%" % (src, dst, 100.0 * done / size))
        os.fsync(fo.fileno())
    dur = max(time.time() - start, 0.001)
    log("     copie terminee : %.1f Gio parcourus, %.1f Gio ecrits, %.0f Mio/s"
        % (done / GIB, written / GIB, done / dur / 1048576))


def find_proxy(p, name):
    if name:
        return find_vm(p, name)
    try:
        with open("/sys/class/dmi/id/product_uuid") as f:
            ident = f.read().strip().lower()
        vm = p.get("/vmm/%s/ahv/config/vms/%s" % (V["vmm"], ident))
        log("proxy detectee : %s" % vm.get("name"))
        return vm
    except Exception as e:
        raise ApiError("proxy non detectee (%s) : preciser --proxy NOM_DE_CETTE_VM" % e)


def need_root():
    if os.geteuid() != 0:
        sys.exit("Cette commande lit des disques : a lancer en root, sur la VM proxy.")


# ---------------------------------------------------------------- commandes

def cmd_check(p, a):
    for api, path in (("dataprotection", "/config/recovery-points"),
                      ("volumes", "/config/volume-groups"),
                      ("vmm", "/ahv/config/vms"),
                      ("clustermgmt", "/config/storage-containers"),
                      ("prism", "/config/tasks")):
        for ver in sorted({V[api], V.get(api + "_create", V[api])}):
            try:
                p.call("GET", "/%s/%s%s?$limit=1" % (api, ver, path))
                log("API %-15s %s : OK" % (api, ver))
            except ApiError as e:
                log("API %-15s %s : ECHEC %s" % (api, ver, str(e)[:200]))
    vm = find_vm(p, a.vm)
    boot = (vm.get("bootConfig") or {}).get("$objectType", "?").split(".")[-1]
    log("VM %s  extId=%s  cluster=%s" % (vm["name"], vm["extId"], (vm.get("cluster") or {}).get("extId")))
    log("   %s socket(s) x %s coeur(s), %.1f Gio RAM, demarrage %s, etat %s, type %s"
        % (vm.get("numSockets"), vm.get("numCoresPerSocket"),
           (vm.get("memorySizeBytes") or 0) / GIB, boot, vm.get("powerState"), vm.get("machineType")))
    for d in vm_disks(vm):
        log("   disque %s.%s  %.1f Gio  conteneur=%s" % (
            d["address"].get("busType"), d["address"].get("index"),
            (d["size"] or 0) / GIB, d["container"]))
    log("   %d carte(s) reseau, %d lecteur(s) CD" % (len(vm.get("nics") or []), len(vm.get("cdRoms") or [])))
    if vm.get("vtpmConfig", {}).get("isVtpmEnabled"):
        log("   ATTENTION : vTPM active (contenu non sauvegardable)")
    log("check termine : rien n'a ete modifie")


def cmd_snapshot(p, a):
    vm = find_vm(p, a.vm)
    rp = create_rp(p, vm, "snap")
    delete_rp(p, rp)
    log("snapshot : OK (creation et suppression d'un point de recuperation)")


def changed_regions(p, rp, disk, ref=None, ref_disk=None, size=0, block=262144):
    """Renvoie (octets REGULAR, octets ZEROED, nombre de regions, nombre d'appels)."""
    dp = V["dataprotection"]

    def dref(r, d):
        return {"$objectType": "dataprotection.v4.content.VmDiskRecoveryPointReference",
                "recoveryPointExtId": r["extId"], "vmRecoveryPointExtId": r["vmrp"],
                "diskRecoveryPointExtId": d["diskRecoveryPointExtId"]}

    spec = {"$objectType": "dataprotection.v4.content.ComputeChangedRegionsClusterDiscoverSpec",
            "diskRecoveryPoint": dref(rp, disk)}
    if ref:
        spec["referenceDiskRecoveryPoint"] = dref(ref, ref_disk)
    doc = p.call("POST", "/dataprotection/%s/config/recovery-points/%s/$actions/discover-cluster"
                 % (dp, rp["extId"]), {"operation": "COMPUTE_CHANGED_REGIONS", "spec": spec})[0]
    info = doc.get("data") or {}
    ip = info.get("clusterIp") or {}
    addr = (ip.get("ipv4") or {}).get("value") or "[%s]" % (ip.get("ipv6") or {}).get("value")
    token = info.get("jwtToken")
    if not token or addr == "[None]":
        raise ApiError("discover-cluster : reponse inattendue : %s" % json.dumps(doc)[:1500])
    log("     cluster a interroger : %s (jeton recu, %d caracteres)" % (addr, len(token)))
    base = "https://%s:9440/api" % addr
    path = ("/dataprotection/%s/content/recovery-points/%s/vm-recovery-points/%s"
            "/disk-recovery-points/%s/$actions/compute-changed-regions"
            % (dp, rp["extId"], rp["vmrp"], disk["diskRecoveryPointExtId"]))
    regular = zeroed = count = calls = 0
    offset, prev_end = 0, -1
    while True:
        body = {"offset": offset, "blockSizeByte": block}
        if ref:
            body.update({"referenceRecoveryPointExtId": ref["extId"],
                         "referenceVmRecoveryPointExtId": ref["vmrp"],
                         "referenceDiskRecoveryPointExtId": ref_disk["diskRecoveryPointExtId"]})
        try:
            doc = p.call("POST", path, body, headers={"Cookie": "NTNX_IGW_SESSION=" + token},
                         base=base, auth=False)[0]
        except ApiError as e:
            if calls == 0:
                raise
            log("     appel suivant refuse a l'offset %d (fin du disque ?) : %s"
                % (offset, str(e)[:300]))
            break
        calls += 1
        regions = doc.get("data") or []
        if isinstance(regions, dict):
            raise ApiError("compute-changed-regions : %s" % json.dumps(regions)[:1500])
        if calls == 1:
            log("     metadonnees de la 1re reponse : %s" % json.dumps(doc.get("metadata"))[:400])
        for r in regions:
            off, ln = r["offset"], r["length"]
            if off < prev_end:
                raise ApiError("regions non triees ou chevauchantes a l'offset %d" % off)
            prev_end = off + ln
            count += 1
            if r.get("regionType") == "ZEROED":
                zeroed += ln
            else:
                regular += ln
        # Une reponse est plafonnee : on reprend apres la derniere region recue.
        if len(regions) < 1000 or prev_end <= offset or (size and prev_end >= size):
            break
        offset = prev_end
    return regular, zeroed, count, calls


def cmd_cbt(p, a):
    vm = find_vm(p, a.vm)
    sizes = {d["diskExtId"]: d["size"] for d in vm_disks(vm)}
    sizes.update({d["extId"]: d["size"] for d in vm_disks(vm)})
    clean = Cleanup()
    try:
        rp1 = create_rp(p, vm, "cbt1")
        clean.add("rp1", lambda: delete_rp(p, rp1))
        for d in rp1["disks"]:
            size = sizes.get(d.get("diskExtId")) or 0
            reg, zer, n, calls = changed_regions(p, rp1, d, size=size)
            log("  COMPLET  disque %s : %.2f Gio de donnees, %.2f Gio de zeros declares, "
                "%d region(s), %d appel(s)%s" % (
                    d.get("diskExtId"), reg / GIB, zer / GIB, n, calls,
                    " ; disque de %.1f Gio -> %.0f %% a lire" % (size / GIB, 100.0 * reg / size)
                    if size else ""))
        log("Attente de %d s : ecrire maintenant quelques fichiers DANS la VM de test "
            "(par ex. dd if=/dev/urandom of=/tmp/essai bs=1M count=200 ; sync)" % a.wait)
        time.sleep(a.wait)
        rp2 = create_rp(p, vm, "cbt2")
        clean.add("rp2", lambda: delete_rp(p, rp2))
        by_disk = {d.get("diskExtId"): d for d in rp1["disks"]}
        for d in rp2["disks"]:
            old = by_disk.get(d.get("diskExtId"))
            if not old:
                log("  disque %s absent du premier point : ignore" % d.get("diskExtId"))
                continue
            reg, zer, n, calls = changed_regions(p, rp2, d, rp1, old,
                                                 size=sizes.get(d.get("diskExtId")) or 0)
            log("  INCREMENTAL disque %s : %.1f Mio modifies, %.1f Mio de zeros, "
                "%d region(s), %d appel(s)" % (d.get("diskExtId"), reg / 1048576,
                                               zer / 1048576, n, calls))
    finally:
        clean.run()
    log("cbt : termine")


def stage_source(p, vm, proxy, clean, label):
    """Point de recuperation -> Volume Group temporaire -> attache a la proxy."""
    rp = create_rp(p, vm, label)
    clean.add("rp", lambda: delete_rp(p, rp))
    vg = create_vg(p, vm["cluster"]["extId"], vm["name"] + "-src")
    clean.add("vg-src", lambda: delete_vg(p, vg))
    for i, d in enumerate(rp["disks"]):
        add_vg_disk(p, vg, i, d["diskRecoveryPointExtId"], "DISK_RECOVERY_POINT")
    disks = vg_disks(p, vg)
    before = block_devices()
    attach(p, vg, proxy)
    clean.add("detach-src", lambda: detach(p, vg, proxy))
    new = wait_new_devices(before, len(disks))
    log("  nouveaux disques sur la proxy : %s" % ", ".join(
        "%s (%.1f Gio)" % (n, s / GIB) for n, s in sorted(new.items())))
    for n in sorted(new):
        describe_device(n)
    return rp, vg, disks, new


def check_same_cluster(vm, proxy):
    if (vm.get("cluster") or {}).get("extId") != (proxy.get("cluster") or {}).get("extId"):
        raise ApiError("la VM et la proxy ne sont pas sur le meme cluster")
    if vm["extId"] == proxy["extId"]:
        raise ApiError("la VM de test ne peut pas etre la proxy elle-meme")


def cmd_read(p, a):
    need_root()
    vm, proxy = find_vm(p, a.vm), find_proxy(p, a.proxy)
    check_same_cluster(vm, proxy)
    clean = Cleanup()
    try:
        _, _, _, new = stage_source(p, vm, proxy, clean, "read")
        for n, size in sorted(new.items()):
            read_device(n, min(size, int(a.max_gib * GIB)))
    finally:
        clean.run()
    log("read : termine")


def cmd_restore(p, a):
    need_root()
    vm, proxy = find_vm(p, a.vm), find_proxy(p, a.proxy)
    check_same_cluster(vm, proxy)
    src_vm_disks = vm_disks(vm)
    new_name = "%s%s-restored-%d" % (PREFIX, vm["name"], int(time.time()))
    clean = Cleanup()
    try:
        rp, _, s_disks, s_new = stage_source(p, vm, proxy, clean, "restore")
        s_devs = match_devices(s_disks, s_new)

        tgt = create_vg(p, vm["cluster"]["extId"], vm["name"] + "-dst")
        clean.add("vg-dst", lambda: delete_vg(p, tgt))
        by_id = {}
        for d in src_vm_disks:
            by_id[d["diskExtId"]] = d
            by_id[d["extId"]] = d
        plan = []
        for i, drp in enumerate(rp["disks"]):
            orig = by_id.get(drp.get("diskExtId"))
            if not orig:
                raise ApiError("disque %s du point introuvable dans la VM" % drp.get("diskExtId"))
            add_vg_disk(p, tgt, i, orig["container"], "STORAGE_CONTAINER",
                        s_disks[i]["diskSizeBytes"])
            plan.append(orig)
        t_disks = vg_disks(p, tgt)
        before = block_devices()
        attach(p, tgt, proxy)
        clean.add("detach-dst", lambda: detach(p, tgt, proxy))
        t_new = wait_new_devices(before, len(t_disks))
        t_devs = match_devices(t_disks, t_new)

        for i, (s, t) in enumerate(zip(s_devs, t_devs)):
            log("  copie du disque %d : /dev/%s -> /dev/%s" % (i, s, t))
            copy_device(s, t, s_disks[i]["diskSizeBytes"])

        detach(p, tgt, proxy)
        clean.drop("detach-dst")

        boot = dict(vm.get("bootConfig") or {})
        boot_type = boot.get("$objectType", "vmm.v4.ahv.config.LegacyBoot")
        new_boot = {"$objectType": boot_type}
        if "Uefi" in boot_type:
            new_boot["isSecureBootEnabled"] = bool(boot.get("isSecureBootEnabled"))
        body = {
            "name": new_name,
            "description": "Essai etape 0 Bareos : copie de %s, a supprimer" % vm["name"],
            "cluster": {"extId": vm["cluster"]["extId"]},
            "numSockets": vm.get("numSockets", 1),
            "numCoresPerSocket": vm.get("numCoresPerSocket", 1),
            "memorySizeBytes": vm.get("memorySizeBytes"),
            "bootConfig": new_boot,
            "disks": [{
                "diskAddress": {k: orig["address"][k] for k in ("busType", "index")
                                if k in orig["address"]},
                "backingInfo": {
                    "$objectType": "vmm.v4.ahv.config.VmDisk",
                    "storageContainer": {"extId": orig["container"]},
                    "dataSource": {"reference": {
                        "$objectType": "vmm.v4.ahv.config.VolumeDiskReference",
                        "volumeGroupExtId": tgt["extId"],
                        "diskExtId": t_disks[i]["extId"]}},
                },
            } for i, orig in enumerate(plan)],
        }
        if vm.get("machineType"):
            body["machineType"] = vm["machineType"]
        p.do("POST", "/vmm/%s/ahv/config/vms" % V["vmm_create"], body,
             "creation de la VM %s (eteinte, sans reseau)" % new_name)
    finally:
        clean.run()
    log("restore : VM '%s' creee, ETEINTE et SANS carte reseau." % new_name)
    log("A faire a la main : la demarrer depuis Prism, verifier a la console qu'elle "
        "demarre, puis lancer : ahv_etape0.py cleanup --yes")


def cmd_cleanup(p, a):
    found = []
    for vm in p.list("/vmm/%s/ahv/config/vms" % V["vmm"]):
        if (vm.get("name") or "").startswith(PREFIX):
            found.append(("VM", vm["name"], "/vmm/%s/ahv/config/vms/%s" % (V["vmm"], vm["extId"])))
    for vg in p.list("/volumes/%s/config/volume-groups" % V["volumes"]):
        if (vg.get("name") or "").startswith(PREFIX):
            found.append(("Volume Group", vg["name"],
                          "/volumes/%s/config/volume-groups/%s" % (V["volumes"], vg["extId"])))
    for rp in p.list("/dataprotection/%s/config/recovery-points" % V["dataprotection"]):
        if (rp.get("name") or "").startswith(PREFIX):
            found.append(("Point de recuperation", rp["name"],
                          "/dataprotection/%s/config/recovery-points/%s"
                          % (V["dataprotection"], rp["extId"])))
    if not found:
        log("cleanup : aucun objet %s* sur le cluster" % PREFIX)
        return
    for kind, name, _ in found:
        log("  %s : %s" % (kind, name))
    if not a.yes:
        log("cleanup : %d objet(s) trouve(s). Relancer avec --yes pour les supprimer." % len(found))
        return
    errors = 0
    for kind, name, path in found:
        try:
            p.delete(path, "%s %s" % (kind, name))
        except ApiError as e:
            errors += 1
            log("  !! %s %s : %s" % (kind, name, e))
    log("cleanup : termine, %d erreur(s)%s" % (
        errors, " (une VM doit etre eteinte, un Volume Group detache)" if errors else ""))
    if errors:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pc", required=True, help="adresse de Prism Central")
    ap.add_argument("--user", default="admin")
    tls = ap.add_mutually_exclusive_group()
    tls.add_argument("--ca", help="certificat de l'autorite interne (PEM)")
    tls.add_argument("--insecure", action="store_true", help="ne pas verifier le certificat")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "snapshot", "cbt", "read", "restore"):
        s = sub.add_parser(name)
        s.add_argument("--vm", required=True, help="nom exact de la VM de TEST")
        if name == "cbt":
            s.add_argument("--wait", type=int, default=120, help="secondes entre les deux points")
        if name in ("read", "restore"):
            s.add_argument("--proxy", help="nom de la VM ou tourne ce script (sinon auto-detecte)")
        if name == "read":
            s.add_argument("--max-gib", type=float, default=10, help="volume lu par disque")
    sub.add_parser("cleanup").add_argument("--yes", action="store_true")
    a = ap.parse_args()

    password = os.environ.get("AHV_PASSWORD") or getpass.getpass("Mot de passe de %s : " % a.user)
    p = Prism(a.pc, a.user, password, a.ca, a.insecure)
    try:
        globals()["cmd_" + a.cmd](p, a)
    except ApiError as e:
        log("ECHEC : %s" % e)
        sys.exit(1)
    except KeyboardInterrupt:
        log("Interrompu. Verifier avec : ahv_etape0.py cleanup")
        sys.exit(130)


if __name__ == "__main__":
    main()
